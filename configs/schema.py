"""Schema cấu hình khớp với layout thật của dự án.

3 tầng:
    config.yaml            -> RootConfig -> [TenantConfig]
    <load_dir>/rules.yaml  -> [RuleDef]
    <load_dir>/<version>/<CODE>/<CODE>_config.json -> {camera_id: CameraConfig}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobDef:
    """Một job test của tenant (SMOKE / REGRESSION...), trỏ tới 1 Google Sheet."""
    name: str
    sheet_id: str = ""
    dir: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JobDef":
        return cls(name=str(d["name"]), sheet_id=d.get("sheet_id", ""), dir=d.get("dir", ""))


@dataclass
class CameraConfig:
    """Config 1 camera trong file <CODE>_config.json (giữ nguyên raw để linh hoạt)."""
    camera_id: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def zones(self) -> list[Any]:
        return self.raw.get("cam_zones", []) or []

    @property
    def threshold(self) -> float | None:
        return self.raw.get("threshold")

    @property
    def target_class(self) -> list[str]:
        return self.raw.get("target_class", []) or []

    @property
    def fps(self) -> int | None:
        return self.raw.get("fps")


@dataclass
class RuleDef:
    """Một rule trong rules.yaml + config camera nạp từ JSON."""
    name: str
    code: str
    sheet: str = ""
    type: str = "SHORT"            # SHORT | LONG
    time_sleep: int = 7
    batch_debug: str = ""
    cameras: dict[str, CameraConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleDef":
        return cls(
            name=str(d["name"]),
            code=str(d.get("code", "")),
            sheet=d.get("sheet", d.get("name", "")),
            type=d.get("type", "SHORT"),
            time_sleep=int(d.get("time_sleep", 7)),
            batch_debug=d.get("batch_debug", "") or "",
        )


@dataclass
class TenantConfig:
    """Một khách hàng. rules nạp lười từ rules.yaml qua loader."""
    name: str
    jobs: list[JobDef] = field(default_factory=list)
    config: str = ""               # tên folder version, vd 'v2.6.0.2'
    version: str = ""
    dir: str = ""
    load_dir: str = ""             # folder dưới configs/tenants/
    rules: list[RuleDef] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TenantConfig":
        return cls(
            name=str(d["name"]),
            jobs=[JobDef.from_dict(j) for j in d.get("jobs", []) or []],
            config=d.get("config", ""),
            version=d.get("version", ""),
            dir=d.get("dir", ""),
            load_dir=d.get("load_dir", ""),
        )

    def job(self, name: str) -> JobDef | None:
        return next((j for j in self.jobs if j.name.upper() == name.upper()), None)

    def rule(self, key: str) -> RuleDef | None:
        k = key.upper()
        return next((r for r in self.rules if r.name.upper() == k or r.code.upper() == k), None)

    def matches(self, key: str) -> bool:
        k = key.strip().lower()
        return k in (self.name.lower(), self.load_dir.lower())


@dataclass
class RootConfig:
    tenants: list[TenantConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RootConfig":
        return cls(tenants=[TenantConfig.from_dict(t) for t in d.get("tenants", []) or []])

    def tenant(self, key: str) -> TenantConfig | None:
        return next((t for t in self.tenants if t.matches(key)), None)
