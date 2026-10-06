"""Publish evidence lên S3 public, gắn link vào Comparison cho report HTML."""
from __future__ import annotations

from ..context import PipelineContext
from .base import Handler


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
