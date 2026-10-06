"""Đọc QR frame_id trong clip evidence (cho QRAlignmentStrategy)."""
from __future__ import annotations

from ..context import PipelineContext
from .base import Handler


class ReadEvidenceQRHandler(Handler):
    """Đọc QR frame_id ở đầu/cuối từng clip evidence -> gắn khoảng frame GỐC vào event.

    Ghi `event[EVENT_QR_FIRST]` / `event[EVENT_QR_LAST]` (int, hoặc None khi không đọc được)
    cho `QRAlignmentStrategy` chấm "evidence có đúng thời điểm vi phạm không". Chỉ có nghĩa
    khi video nguồn đã được nhúng QR (`--embed-qr`). Bỏ qua êm khi không có
    `ctx.evidence_fetcher` (mock / chưa cấu hình file-browser). 1 clip lỗi không hỏng cả run.
    """

    def __init__(self, evidence_field: str = "evidence") -> None:
        super().__init__()
        self.evidence_field = evidence_field

    def process(self, ctx: PipelineContext) -> None:
        if ctx.evidence_fetcher is None:
            ctx.log.info("  bỏ qua đọc QR evidence (chưa cấu hình file-browser / chạy mock)")
            return
        try:
            from qr_video_tagger import QRVideoReader
        except ImportError as exc:
            ctx.log.warning("  bỏ qua đọc QR evidence: thiếu thư viện (%s), xem requirements.txt", exc)
            return
        from validator.qr_alignment_strategy import EVENT_QR_FIRST, EVENT_QR_LAST

        reader = QRVideoReader()
        by_ref: dict[str, tuple[int | None, int | None]] = {}  # 1 clip đọc 1 lần dù nhiều event trỏ tới
        read = no_qr = failed = 0

        for pred in ctx.predictions.values():
            for ev in pred.events or []:
                if not isinstance(ev, dict):
                    continue
                ref = ev.get(self.evidence_field)
                if not ref:
                    continue
                ref = str(ref)
                if ref not in by_ref:
                    try:
                        content, _ = ctx.evidence_fetcher.fetch(ref)
                        by_ref[ref] = reader.read_first_last(content) if content else (None, None)
                    except Exception as exc:  # noqa: BLE001 — 1 clip lỗi không hỏng cả run
                        failed += 1
                        by_ref[ref] = (None, None)
                        ctx.log.warning("  evidence=%s: lỗi tải/đọc QR: %s", ref, exc)
                    else:
                        if None in by_ref[ref]:
                            no_qr += 1
                        else:
                            read += 1
                ev[EVENT_QR_FIRST], ev[EVENT_QR_LAST] = by_ref[ref]

        ctx.log.info("  QR evidence: %d clip đọc được, %d không có QR, %d lỗi", read, no_qr, failed)
