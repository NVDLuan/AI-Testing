"""Điều phối đưa evidence video vào report: fetch (file-browser) -> upload S3 -> URL.

`evidence_field` là tên field trong mỗi event (pred.events) chứa "ref" để tải video.
Bạn điền field này ở bước poll_results; mặc định "evidence_path".

Lỗi từng evidence được nuốt (log warning) để KHÔNG làm hỏng cả report — video nào
publish được thì có link, video lỗi thì bỏ qua.
"""
from __future__ import annotations

import logging
import os
import re

from interfaces.evidence import EvidenceFetcher, EvidenceUploader

log = logging.getLogger("ai_testing.evidence")

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str) -> str:
    return _SAFE.sub("_", text).strip("_") or "x"


class EvidencePublisher:
    def __init__(
        self,
        fetcher: EvidenceFetcher,
        uploader: EvidenceUploader,
        evidence_field: str = "evidence_path",
        key_prefix: str = "reports/evidence",
        work_dir: str = "downloads/evidence",
    ) -> None:
        self._fetcher = fetcher
        self._uploader = uploader
        self._field = evidence_field
        self._key_prefix = key_prefix.strip("/")
        self._work_dir = work_dir

    @staticmethod
    def _resolve(ev: dict, field: str):
        """Lấy giá trị field, hỗ trợ đường dẫn lồng nhau kiểu 'a.b.c'."""
        cur: object = ev
        for part in field.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
            if cur is None:
                return None
        return cur

    def _refs(self, events: list[dict]) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        for ev in events or []:
            if not isinstance(ev, dict):
                continue
            ref = self._resolve(ev, self._field)
            if ref and str(ref) not in seen:
                seen.add(str(ref))
                refs.append(str(ref))
        return refs

    def publish(
        self, customer_id: str, batch_code: str, rule_name: str, events: list[dict]
    ) -> list[str]:
        """Trả về danh sách URL công khai cho các evidence của một batch.

        Key S3: `{key_prefix}/{customer_id}/{batch_code}/{rule_name}/{filename.ext}` (vd
        reports/bv_c/<batch_code>/<rule_name>/<file>). Khi 1 batch có nhiều evidence trùng
        tên file thì các file từ thứ 2 trở đi được thêm tiền tố `NN_` để khỏi đè nhau.
        """
        urls: list[str] = []
        for idx, ref in enumerate(self._refs(events)):
            local = ""
            try:
                local = self._fetcher.fetch(ref, self._work_dir)
                if not local:
                    continue
                name = os.path.basename(local)
                if idx:  # tránh đè khi nhiều evidence cùng tên trong 1 batch
                    name = f"{idx:02d}_{name}"
                key = f"{self._key_prefix}/{_slug(customer_id)}/{_slug(batch_code)}/{_slug(rule_name)}/{name}"
                url = self._uploader.upload(local, key)
                if url:
                    urls.append(url)
            except Exception as exc:  # noqa: BLE001 — không để 1 evidence làm hỏng report
                log.warning("evidence ref=%s lỗi publish: %s", ref, exc)
            finally:
                if local and os.path.exists(local):
                    os.remove(local)
        return urls
