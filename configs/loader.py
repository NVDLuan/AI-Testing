"""Loader 3 tầng: config.yaml -> rules.yaml -> <CODE>_config.json.

Đường dẫn tenant được tính tương đối theo vị trí file config.yaml:
    <config_dir>/tenants/<load_dir>/rules.yaml
    <config_dir>/tenants/<load_dir>/<version>/<CODE>/<CODE>_config.json
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import yaml

from .schema import CameraConfig, RootConfig, RuleDef, TenantConfig

log = logging.getLogger("ai_testing.loader")

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class ConfigLoader:
    """Nạp cấu hình và resolve các path tenant tương đối với config.yaml."""

    def __init__(self, config_path: str) -> None:
        self.config_path = os.path.abspath(config_path)
        self.config_dir = os.path.dirname(self.config_path)
        self.tenants_dir = os.path.join(self.config_dir, "tenants")

    def load_root(self) -> RootConfig:
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = _expand_env(yaml.safe_load(f) or {})
        return RootConfig.from_dict(raw)

    def tenant_dir(self, tenant: TenantConfig) -> str:
        return os.path.join(self.tenants_dir, tenant.load_dir)

    def load_rules(self, tenant: TenantConfig) -> list[RuleDef]:
        """Nạp rules.yaml vào tenant.rules (idempotent)."""
        if tenant.rules:
            return tenant.rules
        path = os.path.join(self.tenant_dir(tenant), "rules.yaml")
        with open(path, "r", encoding="utf-8") as f:
            raw = _expand_env(yaml.safe_load(f) or {})
        tenant.rules = [RuleDef.from_dict(r) for r in raw.get("rules", []) or []]
        log.info("Tenant %s: nạp %d rule từ %s", tenant.name, len(tenant.rules), path)
        return tenant.rules

    def load_cameras(self, tenant: TenantConfig, rule: RuleDef) -> dict[str, CameraConfig]:
        """Nạp <CODE>_config.json -> rule.cameras (idempotent, lỗi -> rỗng + warn)."""
        if rule.cameras:
            return rule.cameras
        path = os.path.join(
            self.tenant_dir(tenant), tenant.config, rule.code, f"{rule.code}_config.json"
        )
        if not os.path.exists(path):
            log.warning("Không thấy camera config: %s", path)
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rule.cameras = {cam_id: CameraConfig(cam_id, cfg) for cam_id, cfg in data.items()}
        log.info("Rule %s: nạp %d camera", rule.name, len(rule.cameras))
        return rule.cameras
