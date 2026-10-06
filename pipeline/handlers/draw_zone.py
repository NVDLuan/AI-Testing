"""Vẽ ảnh debug zone camera cạnh video nguồn trên S3."""
from __future__ import annotations

import os
import tempfile

from ..context import PipelineContext
from .base import Handler


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
