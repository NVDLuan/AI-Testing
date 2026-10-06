"""Chuẩn bị dữ liệu: đọc ground truth -> hỏi video thiếu -> tải + upload video thiếu."""
from __future__ import annotations

from validator.models import VideoRef

from ..context import PipelineContext
from .base import Handler


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
