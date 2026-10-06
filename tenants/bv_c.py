"""BV C — hand-washing."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from adapters import HandWashingHttpClient
from adapters.waiting import WaitingBase
from configs.schema import TenantConfig
from validator.strategies import ValidationStrategy

from .base import TenantFactory
from .options import RunOptions
from .registry import register

if TYPE_CHECKING:
    from adapters.evidence import FileBrowserFetcher

log = logging.getLogger("ai_testing.factory")


@register("bv_c")
class BvCFactory(TenantFactory):
    """BV C: dùng API hand-washing (/api/hand-washing/*) + map cột sheet riêng.

    Sheet thật có cột "Video Name" (kèm đuôi .mp4), "Video Raw" (URL S3 — tạm dùng
    thay cho video_path, sẽ có cột riêng sau), "Expected Status" ("Trigger"/"No trigger")
    và "camera_code" (khớp với camera_id gửi cho API trigger).
    """

    http_client_cls = HandWashingHttpClient
    default_sheet_columns = {
        "video_url": "Video Raw",  # tạm dùng, chưa có cột path video riêng
        "video_name": "Video Name",
        "trigger_status": "Expected Status",
        # 3-state: No Trigger / Trigger (No Violation) / Trigger (Violation)
        "camera": "camera_code",
    }

    def http_client_kwargs(self, opts: RunOptions) -> dict:
        # Upload hand-washing cần kèm file keypoints .txt -> trỏ thư mục recordings.
        return {"recordings_dir": opts.recordings_dir}

    def evidence_fetcher_cls(self) -> type["FileBrowserFetcher"]:
        from adapters.evidence import DownloadEvidenceHandWashingFetcher

        return DownloadEvidenceHandWashingFetcher

    def create_evidence_fetcher(self, tenant: TenantConfig, opts: RunOptions):
        # Chain bv_c chấm theo layer hand-washing (--hw-layers), không có QRAlignmentStrategy
        # -> không tải evidence chỉ để đọc QR.
        if opts.qr_validate:
            log.warning("bv_c chưa hỗ trợ --qr-validate -> bỏ qua đọc QR evidence")
        return None

    def create_validation_chain(self, tenant: TenantConfig, opts: RunOptions) -> list[ValidationStrategy]:
        # Chain layer theo --hw-layers (vd "trigger,violation,sequence,count"). Mỗi layer
        # chạy validate() riêng -> report riêng -> report ra 1 rule ảo "<rule> - <Name>".
        from validator.hand_washing_strategy import (
            CountStrategy, SequenceStrategy, TriggerStrategy, ViolationStrategy,
        )

        layer_factories: dict[str, Callable[[], ValidationStrategy]] = {
            "trigger": TriggerStrategy,
            "violation": ViolationStrategy,
            "sequence": lambda: SequenceStrategy(exact=False),
            "sequence_exact": lambda: SequenceStrategy(exact=True),
            "count": CountStrategy,
        }
        tokens = [t.strip().lower() for t in (opts.hw_layers or "").split(",") if t.strip()]
        unknown = [t for t in tokens if t not in layer_factories]
        if unknown:
            raise ValueError(
                f"--hw-layers có token không hợp lệ: {unknown!r} "
                f"(hợp lệ: {sorted(layer_factories)})"
            )
        return [layer_factories[t]() for t in tokens]

    def create_waiting(self, tenant: TenantConfig, opts: RunOptions) -> WaitingBase:
        from adapters.waiting import WaitingKafka
        return WaitingKafka(opts.kafka_watcher_api_url, endpoint="api/hand-washing/kafka/upload-topic-lag",
                            api_key=opts.api_key)
