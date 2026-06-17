"""Tải video evidence qua API File Browser của bên thứ 3.

Luồng:
    1. POST /api/login {username, password}  -> response.text = auth token
       gửi kèm mọi request sau dưới header `Cookie: auth=<token>`.
    2. GET  /api/raw<file_path>               -> nội dung video (stream về file local).
       Nếu lỗi và path bắt đầu bằng "/evidence", thử lại sau khi bỏ tiền tố đó.

`ref` (giá trị `evidence_field` trong mỗi event do bên thứ 3 trả về) chính là
`file_path` evidence. Cookie cache lại, tự login lại khi gặp 401/403.
"""
from __future__ import annotations

import logging
import os

import requests

from interfaces.evidence import EvidenceFetcher

log = logging.getLogger("ai_testing.filebrowser")


def _safe_basename(file_path: str) -> str:
    base = os.path.basename(file_path.split("?")[0])
    return base or "evidence.mp4"


class FileBrowserFetcher(EvidenceFetcher):
    API_LOGIN = "/api/login"
    API_RAW = "/api/raw"

    def __init__(
        self,
        base_url: str,
        user: str = "",
        password: str = "",
        timeout_sec: float = 20.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user = user
        self._password = password
        self._timeout_sec = timeout_sec
        self._session = requests.Session()
        self._cookie: str | None = None  # token auth, cache giữa các lần tải

    def _login(self) -> bool:
        """Lấy token auth (response.text) -> cache. Trả True nếu thành công."""
        try:
            resp = self._session.post(
                f"{self._base_url}{self.API_LOGIN}",
                json={"username": self._user, "password": self._password},
                timeout=self._timeout_sec,
            )
        except requests.RequestException as exc:
            log.error("File Browser login lỗi mạng: %s", exc)
            return False
        if resp.status_code != 200:
            log.warning("File Browser login thất bại (HTTP %s): %s",
                        resp.status_code, resp.text[:200])
            return False
        self._cookie = resp.text
        return True

    def _headers(self) -> dict[str, str]:
        if self._cookie is None and not self._login():
            return {}
        return {"Cookie": f"auth={self._cookie}"}

    def _get_raw(self, file_path: str) -> requests.Response | None:
        """GET /api/raw<file_path>, tự login lại 1 lần nếu hết hạn (401/403)."""
        url = f"{self._base_url}{self.API_RAW}{file_path}"
        resp = self._session.get(
            url, headers=self._headers(), stream=True, timeout=self._timeout_sec
        )
        if resp.status_code in (401, 403):
            resp.close()
            self._cookie = None  # ép login lại
            resp = self._session.get(
                url, headers=self._headers(), stream=True, timeout=self._timeout_sec
            )
        return resp

    def fetch(self, ref: str, dest_dir: str) -> str:
        file_path = ref if ref.startswith("/") else f"/{ref}"
        resp = self._get_raw(file_path)

        # Fallback: một số path có tiền tố "/evidence" thừa so với root của API raw.
        if resp is not None and resp.status_code != 200 and file_path.startswith("/evidence"):
            resp.close()
            resp = self._get_raw(file_path[len("/evidence"):])

        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else "n/a"
            log.warning("File Browser tải evidence thất bại: path=%s status=%s", ref, status)
            if resp is not None:
                resp.close()
            return ""

        os.makedirs(dest_dir, exist_ok=True)
        local = os.path.join(dest_dir, _safe_basename(file_path))
        with resp:
            with open(local, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):  # 1MB
                    if chunk:
                        f.write(chunk)
        log.info("File Browser tải %s -> %s", ref, local)
        return local
