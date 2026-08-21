"""WaitingBase + implementations — chờ AI xử lý xong trước khi lấy kết quả.

Mỗi cách chờ 1 module riêng: `timeout` (ngủ cố định), `kafka` (poll consumer lag),
`grafana` (poll log Loki), `parallel` (chạy nhiều waiter đồng thời, AND).
"""
from __future__ import annotations

from .base import WaitingBase
from .grafana import WaitingGrafana
from .kafka import WaitingKafka
from .parallel import WaitingParallel
from .timeout import WaitingTimeout

__all__ = [
    "WaitingBase",
    "WaitingGrafana",
    "WaitingKafka",
    "WaitingParallel",
    "WaitingTimeout",
]
