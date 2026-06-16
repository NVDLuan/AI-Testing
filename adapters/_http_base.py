"""Phần dùng chung cho các HTTP client (FakeAgent API).

base_url là gốc host, vd: http://103.104.122.36:8000
Auth qua header X-API-Key. /health là public; /api/* cần key.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

import requests

from interfaces.analysis_client import HealthStatus

log = logging.getLogger("ai_testing.http")


def norm_name(name: str) -> str:
    """Chuẩn hoá tên video để so khớp (bỏ path + đuôi mở rộng)."""
    base = os.path.basename(name.split("?")[0])
    return base.rsplit(".", 1)[0] if "." in base else base


class BaseHttpClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        poll_interval_sec: float = 5.0,
        timeout_sec: float = 600.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._poll_interval_sec = poll_interval_sec
        self._timeout_sec = timeout_sec
        self._session = requests.Session()
        if api_key:
            self._session.headers["X-API-Key"] = api_key

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def make_batch_code(self, rule_code: str) -> str:
        return f"{rule_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def health_check(self) -> HealthStatus:
        detail: dict = {}
        # 1. /health công khai
        r = self._session.get(self._url("/health"), timeout=10)
        r.raise_for_status()
        detail["health"] = r.json()
        healthy = str(detail["health"].get("status", "")).lower() == "ok"

        # 2. tài nguyên + deepstream (cần API key)
        if self._api_key:
            try:
                r2 = self._session.get(self._url("/api/get-overall-health-status"), timeout=10)
                if r2.ok:
                    sys_status = r2.json()
                    detail["system"] = sys_status
                    if sys_status.get("deepstream_is_healthy") is False:
                        healthy = False
                else:
                    # Có key nhưng /api từ chối -> test thật sẽ fail, coi như không khoẻ.
                    detail["system_error"] = f"HTTP {r2.status_code}: {r2.text[:200]}"
                    healthy = False
            except requests.RequestException as exc:  # pragma: no cover
                detail["system_error"] = str(exc)
        return HealthStatus(healthy, detail)
