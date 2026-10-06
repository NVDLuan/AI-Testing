"""Helper dựng waiter dùng chung giữa nhiều tenant."""
from __future__ import annotations

from adapters.waiting import WaitingBase

from .options import RunOptions


def build_grafana_waiting(opts: RunOptions) -> WaitingBase:
    """Dựng `WaitingGrafana` từ `RunOptions` — dùng chung cho các tenant cần chờ qua
    log Loki (Grafana), tránh lặp lại parse `grafana_target_logs` ở từng factory.
    """
    from adapters.waiting import WaitingGrafana

    target_logs = [s.strip() for s in opts.grafana_target_logs.split(",") if s.strip()]
    return WaitingGrafana(
        base_url=opts.grafana_url,
        username=opts.grafana_username,
        password=opts.grafana_password,
        server_name=opts.grafana_server_name,
        service_name=opts.grafana_service_name,
        target_logs=target_logs or ["[BOOT] No config message yet, still waiting..."],
        poll_interval=int(opts.grafana_poll_interval_sec),
    )
