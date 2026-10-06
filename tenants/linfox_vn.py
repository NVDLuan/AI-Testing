"""Linfox VN (+ Karter School dùng chung workflow)."""
from __future__ import annotations

from adapters import LinfoxHttpClient
from adapters.waiting import WaitingBase
from configs.schema import TenantConfig

from .base import TenantFactory
from .options import RunOptions
from .registry import register
from .waiting import build_grafana_waiting


@register("linfox_vn")
class LinfoxVNFactory(TenantFactory):
    """Linfox VN: generic API + map cột sheet thật khi đọc Google Sheet."""

    http_client_cls = LinfoxHttpClient
    default_sheet_columns = {
        "video_url": "Video Raw",  # tạm dùng, chưa có cột path video riêng
        "video_name": "Video Name",
        "expected": "Expected Status",  # "Approve" = có vi phạm
        "camera": "Camera Code",
    }

    def create_waiting(self, tenant: TenantConfig, opts: RunOptions) -> WaitingBase:
        from adapters.waiting import WaitingKafka
        return WaitingKafka(opts.kafka_watcher_api_url)


@register("karter_school")
class KarterSchoolFactory(LinfoxVNFactory):
    """Karter School: dùng chung workflow Linfox VN (generic API + map cột sheet) —
    khác data (rules/cameras/sheet riêng ở `configs/tenants/karter_school/`) và khác
    cách chờ: cần CẢ Kafka lag=0 LẪN log Loki idle (`WaitingParallel`, chạy đồng thời,
    AND — xem `adapters.waiting.WaitingParallel`). Đăng ký factory riêng (thay vì gắn
    thêm `@register` lên `LinfoxVNFactory`) để dễ theo dõi và tách hành vi sau này mà
    không đụng linfox_vn.
    """

    def create_waiting(self, tenant: TenantConfig, opts: RunOptions) -> WaitingBase:
        from adapters.waiting import WaitingKafka, WaitingParallel
        opts.grafana_target_logs = "[DONE] STAGE 1/3 (ds-app, detection) FINISHED ALL"

        return WaitingParallel([
            WaitingKafka(opts.kafka_watcher_api_url),
            build_grafana_waiting(opts),
        ])
