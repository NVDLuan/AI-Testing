"""Các handler của pipeline theo mẫu Chain of Responsibility.

Mỗi handler làm đúng MỘT việc rồi chuyển context cho handler kế tiếp:

    LoadGroundTruth -> CheckMissing -> Upload -> Trigger
        -> WaitResults -> Validate -> Report

Thêm/bớt/đổi thứ tự bước = sửa danh sách trong chain.build_default_chain().
"""
from __future__ import annotations

import os
import tempfile
import time
from abc import ABC, abstractmethod
from datetime import datetime

from validator.models import VideoRef
from validator.strategies import default_strategy
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
            videos = ctx.videos_by_rule.get(rule.code)
            if not videos:
                continue
            ctx.client.update_rule_config(rule, videos)


class TriggerHandler(Handler):
    """Trigger phân tích theo từng rule rồi chờ kết quả (gộp để giữ ngữ cảnh rule)."""

    @staticmethod
    def make_batch_code(customer: str, rule_code: str) -> str:
        return f"{customer.replace(' ', '_')}_{rule_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # return "Karter_School_CNT08_20260806_152825"

    def process(self, ctx: PipelineContext) -> None:
        rule_by_code = {r.code: r for r in ctx.rules}
        for code, videos in ctx.videos_by_rule.items():
            if not videos:
                continue
            rule = rule_by_code[code]
            batch_code = self.make_batch_code(ctx.tenant.name, rule.code)
            ctx.client.trigger(videos, rule, batch_code)
            ctx.batch_codes[code] = batch_code
            ctx.log.info("  rule=%s: trigger job=%s", rule.name, batch_code)


class WaitHandler(Handler):

    def process(self, ctx: PipelineContext) -> None:
        ctx.log.info("Waiting Result Return..........................")
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
    """So khớp prediction với ground truth -> metrics.

    Chạy `validate()` cho TỪNG strategy trong `ctx.strategy_chain` (rỗng -> 1 strategy
    tự chọn theo dữ liệu), mỗi strategy 1 report riêng lưu vào `ctx.reports[name]` —
    report coi mỗi report là 1 "rule ảo" (xem `report/template.py`).
    """

    def process(self, ctx: PipelineContext) -> None:
        chain = ctx.strategy_chain or [default_strategy(ctx.ground_truth)]
        ctx.reports = {}
        for strat in chain:
            rep = validate(ctx.ground_truth, ctx.predictions, strat)
            ctx.reports[strat.name] = rep
            ov = rep.overall
            ctx.log.info(
                "  [%s] TP=%d FP=%d FN=%d TN=%d | Recall=%.3f FAR=%.3f",
                strat.name, ov.tp, ov.fp, ov.fn, ov.tn, ov.recall, ov.far,
            )

def _extract_first_frame(video_bytes: bytes):
    """Tách frame đầu của video (cv2 cần path/stream nên phải ghi tạm ra đĩa)."""
    import cv2

    fd, path = tempfile.mkstemp(suffix=".mp4")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(video_bytes)
        cap = cv2.VideoCapture(path)
        try:
            ok, frame = cap.read()
            return frame if ok else None
        finally:
            cap.release()
    finally:
        os.unlink(path)


class DrawZoneHandler(Handler):
    """Vẽ ảnh minh hoạ zone camera cạnh video nguồn trên S3 (debug cho QC).

    Bỏ qua êm khi: `video_store` không phải S3 (mock/offline), thiếu opencv-python,
    hoặc video không có camera config khớp. Idempotent: ảnh đã tồn tại ở đúng vị trí
    `report.template.image_config_url` sẽ trỏ tới thì bỏ qua, không vẽ+upload lại.
    """

    def process(self, ctx: PipelineContext) -> None:
        from adapters.storage import S3VideoStore

        if not isinstance(ctx.video_store, S3VideoStore):
            ctx.log.info("  bỏ qua vẽ zone (video_store không phải S3, vd mock/offline)")
            return
        try:
            import cv2
        except ImportError:
            ctx.log.warning("  bỏ qua vẽ zone: chưa cài opencv-python (xem requirements.txt)")
            return
        from adapters.visualization import ZoneDrawer
        from report.template import image_config_url

        version_config = ctx.tenant.config or ctx.tenant.version
        drawer = ZoneDrawer()
        drawn = skipped = failed = 0

        for rule in ctx.rules:
            for video in ctx.videos_by_rule.get(rule.code) or []:
                if not video.url:
                    continue
                img_url = image_config_url(video.url, version_config)
                if not img_url:
                    continue
                try:
                    if ctx.video_store.object_exists(img_url):
                        skipped += 1
                        continue

                    camera = rule.cameras.get(video.camera_id)
                    if camera is None:
                        ctx.log.warning(
                            "  video=%s: không có camera config (camera_id=%s) -> bỏ qua vẽ zone",
                            video.name, video.camera_id,
                        )
                        continue

                    content, _ = ctx.video_store.fetch(video)
                    if not content:
                        ctx.log.error("  video=%s: không tải được video nguồn -> bỏ qua vẽ zone", video.name)
                        continue

                    frame = _extract_first_frame(content)
                    if frame is None:
                        ctx.log.error("  video=%s: không tách được frame đầu -> bỏ qua vẽ zone", video.name)
                        continue

                    frame = drawer.draw(frame, camera.raw)
                    ok, buf = cv2.imencode(".webp", frame, [cv2.IMWRITE_WEBP_QUALITY, 85])
                    if not ok:
                        ctx.log.error("  video=%s: encode webp thất bại", video.name)
                        continue

                    ctx.video_store.upload_bytes(img_url, buf.tobytes(), "image/webp")
                    drawn += 1
                except Exception as exc:  # noqa: BLE001 — 1 video lỗi không hỏng cả run
                    failed += 1
                    ctx.log.warning("  video=%s: lỗi vẽ/upload zone: %s", video.name, exc)

        ctx.log.info("  zone visualization: %d vẽ mới, %d đã có sẵn, %d lỗi", drawn, skipped, failed)




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
        if not ctx.reports:
            return
        customer = ctx.tenant.load_dir or ctx.tenant.name
        rule_name_by_code = {r.code: r.name for r in ctx.rules}
        published = 0
        urls_by_video: dict[str, list[str]] = {}
        # Nhiều layer (strategy) có thể cùng nhắc tới 1 video -> urls_by_video dùng
        # CHUNG cho mọi report để khỏi publish trùng (dedup theo video_id).
        for rep in ctx.reports.values():
            for cmp in rep.comparisons:
                # 1 video có thể sinh > 1 Comparison (1 outcome/event) -> publish evidence 1
                # lần/video (khỏi upload trùng), rồi gắn cùng list URL cho mọi dòng của video đó.
                if cmp.video_id in urls_by_video:
                    cmp.detail = {**(cmp.detail or {}), "evidence_urls": urls_by_video[cmp.video_id]}
                    continue
                pred = ctx.predictions.get(cmp.video_id)
                if pred is None or not pred.events:
                    continue
                # video_id = "<rule_code>/<video_name>"; batch_code lấy từ job trigger của rule.
                rule_code = cmp.video_id.split("/", 1)[0]
                batch_code = ctx.batch_codes.get(rule_code, rule_code)
                rule_name = rule_name_by_code.get(rule_code, rule_code)
                urls = ctx.evidence_publisher.publish(customer, batch_code, rule_name, pred.events)
                if urls:
                    urls_by_video[cmp.video_id] = urls
                    cmp.detail = {**(cmp.detail or {}), "evidence_urls": urls}
                    published += len(urls)
        ctx.log.info("  đã publish %d evidence lên S3", published)


class ReportHandler(Handler):
    """Xuất báo cáo (console/json/csv/html). Luôn chạy — kể cả khi pipeline bị dừng."""

    run_on_abort = True

    def process(self, ctx: PipelineContext) -> None:
        # import cục bộ để tránh phụ thuộc vòng khi chỉ dùng pipeline ở nơi khác.
        from report.reporter import write_reports

        aborted = ctx.aborted or not ctx.reports
        # 'json'/'template' -> file test_results_*.json (mẫu QC) khi chạy XONG.
        # 'html' LUÔN dùng dashboard dựng từ schema QC này (không còn phụ thuộc phải
        # kèm 'json'/'template' trong --formats) -> đây là đường HTML chuẩn duy nhất
        # cho lượt chạy thành công; đường HTML bảng đơn giản cũ chỉ còn dùng khi ABORT
        # (không có comparison để dựng dashboard). Khi abort thì không có kết quả ->
        # 'json' rơi về *_ABORTED.json của write_reports.
        emit_template = (not aborted) and (
                "template" in ctx.report_formats
                or "json" in ctx.report_formats
                or "html" in ctx.report_formats
        )
        emit_dashboard = emit_template and ("html" in ctx.report_formats)
        passthrough = [f for f in ctx.report_formats if f != "template"]
        if emit_template:
            passthrough = [f for f in passthrough if f != "json"]
        if emit_dashboard:
            passthrough = [f for f in passthrough if f != "html"]

        # Tách report theo tenant/version -> test nhiều tenant song song không lẫn
        # file vào nhau: reports/<tenant>/<version>/<file>.
        tenant_slug = ctx.tenant.load_dir or ctx.tenant.name
        version = ctx.tenant.config or ctx.tenant.version or "unversioned"
        output_dir = os.path.join(ctx.report_dir, tenant_slug, version)

        title = f"{ctx.tenant.name} — job {ctx.job.name} (v{ctx.tenant.version})"
        written = write_reports(
            title=title,
            customer_id=tenant_slug,
            reports=ctx.reports,
            output_dir=output_dir,
            formats=passthrough,
            status="aborted" if ctx.aborted else "ok",
            reason=ctx.abort_reason,
            health=ctx.health,
        )
        for fmt, path in written.items():
            if path:
                ctx.log.info("  report[%s] -> %s", fmt, path)

        if emit_template:
            from report.template import write_template

            json_path = write_template(ctx, output_dir=output_dir)
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
