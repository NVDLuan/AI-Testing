"""Các handler của pipeline theo mẫu Chain of Responsibility.

Mỗi handler làm đúng MỘT việc rồi chuyển context cho handler kế tiếp:

    LoadGroundTruth -> CheckMissing -> Upload -> Trigger
        -> WaitResults -> Validate -> Report

Thêm/bớt/đổi thứ tự bước = sửa danh sách trong chain.build_default_chain().
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime

from validator.models import VideoRef
from validator.validator import validate

from .context import PipelineContext


class Handler(ABC):
    """Một mắt xích. Trả về context để các handler nối tiếp nhau.

    Khi pipeline đã abort (vd health fail), các handler thường bị BỎ QUA nhưng chain
    vẫn chạy tiếp tới cuối; handler đặt `run_on_abort = True` (vd Report) vẫn chạy.
    """

    run_on_abort: bool = False

    def __init__(self) -> None:
        self._next: Handler | None = None

    def set_next(self, nxt: "Handler") -> "Handler":
        self._next = nxt
        return nxt  # cho phép nối chuỗi .set_next().set_next()

    def handle(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.aborted and not self.run_on_abort:
            ctx.log.info("⊘ bỏ qua %s (đã dừng: %s)", self.__class__.__name__, ctx.abort_reason)
        else:
            ctx.log.info("→ %s", self.__class__.__name__)
            self.process(ctx)
        if self._next is not None:
            return self._next.handle(ctx)
        return ctx

    @abstractmethod
    def process(self, ctx: PipelineContext) -> None:
        ...


class HealthCheckHandler(Handler):
    """Kiểm tra sức khoẻ hệ thống bên thứ 3 TRƯỚC khi test. Không khoẻ -> dừng."""

    def process(self, ctx: PipelineContext) -> None:
        status = ctx.client.health_check()
        ctx.health = status
        sys_info = status.detail.get("system", {})
        if sys_info:
            ctx.log.info(
                "  health: deepstream=%s cpu=%.0f%% ram=%.0f%% gpu=%.0f%% disk=%.0f%%",
                sys_info.get("deepstream_is_healthy"),
                sys_info.get("cpu_usage_percentage", 0),
                sys_info.get("ram_usage_percentage", 0),
                sys_info.get("gpu_usage_percentage", 0),
                sys_info.get("disk_usage_percentage", 0),
            )
        if not status.healthy:
            over = status.detail.get("over_threshold")
            reason = "Health check thất bại"
            if over:
                reason = f"Health check thất bại: vượt ngưỡng {', '.join(over)}"
            ctx.abort(reason)  # chi tiết đầy đủ nằm ở ctx.health (in trong report)
        else:
            ctx.log.info("  health: OK")


class LoadGroundTruthHandler(Handler):
    """Đọc sheet/nguồn ground truth cho từng rule, dựng danh sách video theo rule."""

    def process(self, ctx: PipelineContext) -> None:
        for rule in ctx.rules:
            ctx.loader.load_cameras(ctx.tenant, rule)  # cần camera_id cho video ref
            records = ctx.gt_source.load(ctx.job, rule)
            ctx.ground_truth.extend(records)
            ctx.videos_by_rule[rule.code] = [
                VideoRef(r.video_id, r.video_url, r.rule_id, r.camera_id, r.video_name)
                for r in records
            ]
            ctx.log.info("  rule=%s: %d video", rule.name, len(records))
        ctx.client.prime(ctx.ground_truth)


class CheckMissingHandler(Handler):
    """Hỏi API xem video nào còn thiếu trên hệ thống."""

    def process(self, ctx: PipelineContext) -> None:
        all_videos = [v for vids in ctx.videos_by_rule.values() for v in vids]
        ctx.missing = ctx.client.check_missing(all_videos)
        ctx.log.info("  thiếu %d/%d video", len(ctx.missing), len(all_videos))


class UploadHandler(Handler):
    """Lấy video thiếu từ kho nguồn (S3) vào RAM rồi upload thẳng lên hệ thống bên thứ 3."""

    def process(self, ctx: PipelineContext) -> None:
        if not ctx.missing:
            ctx.log.info("  không có video thiếu, bỏ qua upload")
            return
        ok = 0
        for v in ctx.missing:
            content, filename = ctx.video_store.fetch(v)
            ctx.client.upload(v, content or None, filename)
            ok += 1
        ctx.log.info("  đã tải+upload %d/%d video", ok, len(ctx.missing))


class UpdateRuleConfigHandler(Handler):
    """Đẩy config camera của từng rule lên hệ thống (generic API).

    Hand-washing client để no-op nên handler này tự động bị bỏ qua với bv_c.
    """

    def process(self, ctx: PipelineContext) -> None:
        for rule in ctx.rules:
            if not ctx.videos_by_rule.get(rule.code):
                continue
            ctx.client.update_rule_config(rule)


class TriggerAndWaitHandler(Handler):
    """Trigger phân tích theo từng rule rồi chờ kết quả (gộp để giữ ngữ cảnh rule)."""

    @staticmethod
    def make_batch_code(customer: str, rule_code: str) -> str:
        return f"{customer}_{rule_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def process(self, ctx: PipelineContext) -> None:
        rule_by_code = {r.code: r for r in ctx.rules}
        for code, videos in ctx.videos_by_rule.items():
            if not videos:
                continue
            rule = rule_by_code[code]
            batch_code = self.make_batch_code(ctx.tenant.name, rule.code)
            job_id = ctx.client.trigger(videos, rule, batch_code)
            ctx.batch_codes[code] = job_id
            ctx.log.info("  rule=%s: trigger job=%s", rule.name, job_id)


class WaitHandler(Handler):

    def process(self, ctx: PipelineContext) -> None:
        ctx.log.info("Waiting")
        ctx.waiting.wait()


class GetResultsHandler(Handler):

    def process(self, ctx: PipelineContext) -> None:
        rule_by_code = {r.code: r for r in ctx.rules}
        for code, videos in ctx.videos_by_rule.items():
            rule = rule_by_code[code]
            job_id = ctx.batch_codes.get(code, "")
            preds = ctx.client.poll_results(job_id, videos)
            ctx.predictions.update(preds)
            ctx.log.info("  rule=%s: nhận %d kết quả", rule.name, len(preds))


class ValidateHandler(Handler):
    """So khớp prediction với ground truth -> metrics."""

    def process(self, ctx: PipelineContext) -> None:
        ctx.report = validate(ctx.ground_truth, ctx.predictions, ctx.strategy)
        ov = ctx.report.overall
        ctx.log.info(
            "  TP=%d FP=%d FN=%d TN=%d | Recall=%.3f FAR=%.3f",
            ov.tp, ov.fp, ov.fn, ov.tn, ov.recall, ov.far,
        )


class PublishEvidenceHandler(Handler):
    """Đưa video evidence lên S3 rồi gắn link vào từng Comparison (cho report HTML).

    Chỉ chạy khi: có `ctx.evidence_publisher` và report cần HTML. Bỏ qua êm nếu không
    cấu hình (offline/mock) — report HTML vẫn ra, chỉ thiếu link.
    """

    def process(self, ctx: PipelineContext) -> None:
        if "html" not in ctx.report_formats:
            ctx.log.info("  bỏ qua publish evidence (không xuất HTML)")
            return
        if ctx.evidence_publisher is None:
            ctx.log.info("  bỏ qua publish evidence (chưa cấu hình file-browser/S3)")
            return
        if ctx.report is None:
            return
        customer = ctx.tenant.load_dir or ctx.tenant.name
        rule_name_by_code = {r.code: r.name for r in ctx.rules}
        published = 0
        for cmp in ctx.report.comparisons:
            pred = ctx.predictions.get(cmp.video_id)
            if pred is None or not pred.events:
                continue
            # video_id = "<rule_code>/<video_name>"; batch_code lấy từ job trigger của rule.
            rule_code = cmp.video_id.split("/", 1)[0]
            batch_code = ctx.batch_codes.get(rule_code, rule_code)
            rule_name = rule_name_by_code.get(rule_code, rule_code)
            urls = ctx.evidence_publisher.publish(customer, batch_code, rule_name, pred.events)
            if urls:
                cmp.detail = {**(cmp.detail or {}), "evidence_urls": urls}
                published += len(urls)
        ctx.log.info("  đã publish %d evidence lên S3", published)


class ReportHandler(Handler):
    """Xuất báo cáo (console/json/csv/html). Luôn chạy — kể cả khi pipeline bị dừng."""

    run_on_abort = True

    def process(self, ctx: PipelineContext) -> None:
        # import cục bộ để tránh phụ thuộc vòng khi chỉ dùng pipeline ở nơi khác.
        from report.reporter import write_reports

        aborted = ctx.aborted or ctx.report is None
        # 'json' và 'template' -> file test_results_*.json (mẫu QC) khi chạy XONG.
        # Khi abort thì không có kết quả -> 'json' rơi về *_ABORTED.json của write_reports.
        emit_template = (not aborted) and (
                "template" in ctx.report_formats or "json" in ctx.report_formats
        )
        emit_dashboard = emit_template and ("html" in ctx.report_formats)
        passthrough = [f for f in ctx.report_formats if f != "template"]
        if emit_template:
            passthrough = [f for f in passthrough if f != "json"]
        if emit_dashboard:
            passthrough = [f for f in passthrough if f != "html"]

        title = f"{ctx.tenant.name} — job {ctx.job.name} (v{ctx.tenant.version})"
        written = write_reports(
            title=title,
            customer_id=ctx.tenant.load_dir or ctx.tenant.name,
            report=ctx.report,
            output_dir=ctx.report_dir,
            formats=passthrough,
            status="aborted" if ctx.aborted else "ok",
            reason=ctx.abort_reason,
            health=ctx.health,
        )
        for fmt, path in written.items():
            if path:
                ctx.log.info("  report[%s] -> %s", fmt, path)

        if emit_template:
            import os

            from report.template import write_template

            json_path = write_template(ctx, output_dir=ctx.report_dir)
            ctx.log.info("  report[template] -> %s", json_path)

            # Gen dashboard HTML từ file JSON vừa ghi (report/generate_report.py).
            if emit_dashboard:
                from report.generate_report import generate_report as _gen_dashboard

                tpl = os.path.join(os.path.dirname(__file__), "..", "report", "dashboard_template.html")
                tpl = os.path.abspath(tpl)
                html_path = json_path[:-5] + ".html" if json_path.endswith(".json") else json_path + ".html"
                try:
                    if _gen_dashboard(json_path, html_path, tpl):
                        ctx.log.info("  report[html] -> %s", html_path)
                except Exception as exc:  # noqa: BLE001 — không để lỗi gen HTML làm hỏng run
                    ctx.log.warning("  gen dashboard HTML lỗi: %s", exc)
