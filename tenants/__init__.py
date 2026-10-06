"""Factory tạo thành phần pipeline cho từng tenant.

Mặc định mọi tenant dùng `TenantFactory` (`base.py`). Khi một khách hàng cần hành vi
riêng (API khác, cột sheet khác, chain khác...), tạo subclass trong 1 module riêng
(`tenants/<load_dir>.py`), `@register("<load_dir>")`, rồi import module đó dưới đây để
đăng ký. Runner chỉ gọi `get_factory(tenant)` — không cần biết tenant nào đặc biệt.
"""
from .base import TenantFactory
from .options import RunOptions
from .registry import get_factory, register

# Import để chạy @register — thêm tenant mới thì thêm dòng ở đây.
from . import bv_c, ews_usa, linfox_vn  # noqa: E402,F401

__all__ = ["RunOptions", "TenantFactory", "get_factory", "register"]
