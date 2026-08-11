"""Hợp đồng cho luồng đưa video evidence vào report.

Hai bước tách rời để dễ thay thế:
    EvidenceFetcher : từ một "ref" (giá trị field do bên thứ 3 trả về) -> tải vào RAM.
    EvidenceUploader: từ bytes trong RAM -> đẩy lên kho công khai -> trả URL nhúng được.

Toàn bộ đi qua RAM (không ghi file tạm ra đĩa) — cùng triết lý với
`interfaces.video_store.VideoStore.fetch` (tải video nguồn cũng chỉ vào RAM rồi upload
thẳng), đỡ tốn I/O đĩa khi publish nhiều evidence.

`NoOp*` cho chạy offline / khi chưa cấu hình (report vẫn ra, chỉ thiếu link).
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class EvidenceFetcher(ABC):
    @abstractmethod
    def fetch(self, ref: str) -> tuple[bytes, str]:
        """Tải video evidence ứng với `ref` vào RAM. Trả (nội dung bytes, tên file)."""
        raise NotImplementedError


class EvidenceUploader(ABC):
    @abstractmethod
    def upload(self, content: bytes, filename: str, key: str) -> str:
        """Đẩy bytes trong RAM lên kho với `key`, trả URL công khai để nhúng vào report."""
        raise NotImplementedError


class NoOpEvidenceFetcher(EvidenceFetcher):
    def fetch(self, ref: str) -> tuple[bytes, str]:
        return b"", ""


class NoOpEvidenceUploader(EvidenceUploader):
    def upload(self, content: bytes, filename: str, key: str) -> str:
        return ""
