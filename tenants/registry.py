"""Registry override factory theo `load_dir` của tenant."""
from __future__ import annotations

from typing import Callable

from configs.schema import TenantConfig

from .base import TenantFactory

_REGISTRY: dict[str, type[TenantFactory]] = {}


def register(load_dir: str) -> Callable[[type[TenantFactory]], type[TenantFactory]]:
    def deco(cls: type[TenantFactory]) -> type[TenantFactory]:
        _REGISTRY[load_dir] = cls
        return cls

    return deco


def get_factory(tenant: TenantConfig) -> TenantFactory:
    return _REGISTRY.get(tenant.load_dir, TenantFactory)()
