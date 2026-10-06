"""Giữ tương thích import cũ (`from tenants.factory import ...`). Code mới nằm ở:

- `options.py`  — `RunOptions`
- `base.py`     — `TenantFactory` mặc định
- `registry.py` — `register` / `get_factory`
- `waiting.py`  — helper waiter dùng chung (`build_grafana_waiting`)
- `<load_dir>.py` — factory riêng từng tenant (`linfox_vn`, `ews_usa`, `bv_c`)
"""
from .base import TenantFactory
from .bv_c import BvCFactory
from .ews_usa import EwsUsaFactory
from .linfox_vn import KarterSchoolFactory, LinfoxVNFactory
from .options import RunOptions
from .registry import get_factory, register

__all__ = [
    "BvCFactory", "EwsUsaFactory", "KarterSchoolFactory", "LinfoxVNFactory",
    "RunOptions", "TenantFactory", "get_factory", "register",
]
