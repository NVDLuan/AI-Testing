"""EWS USA."""
from __future__ import annotations

from adapters.waiting import WaitingBase
from configs.schema import TenantConfig

from .base import TenantFactory
from .options import RunOptions
from .registry import register
from .waiting import build_grafana_waiting


@register("ews_usa")
class EwsUsaFactory(TenantFactory):
    """EWS USA: chờ AI rảnh qua log Loki (Grafana) thay vì Kafka lag."""

    def create_waiting(self, tenant: TenantConfig, opts: RunOptions) -> WaitingBase:
        return build_grafana_waiting(opts)
