"""Hợp đồng cho luồng đưa video evidence vào report.

Hai bước tách rời để dễ thay thế:
    EvidenceFetcher : từ một "ref" (giá trị field do bên thứ 3 trả về) -> tải file local.
    EvidenceUploader: từ file local -> đẩy lên kho công khai -> trả URL nhúng được.

`NoOp*` cho chạy offline / khi chưa cấu hình (report vẫn ra, chỉ thiếu link).
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class EvidenceFetcher(ABC):
    @abstractmethod
    def fetch(self, ref: str, dest_dir: str) -> str:
        """Tải video evidence ứng với `ref` về `dest_dir`. Trả đường dẫn file local."""
        raise NotImplementedError


class EvidenceUploader(ABC):
    @abstractmethod
    def upload(self, local_path: str, key: str) -> str:
        """Đẩy file local lên kho với `key`, trả về URL công khai để nhúng vào report."""
        raise NotImplementedError


class NoOpEvidenceFetcher(EvidenceFetcher):
    def fetch(self, ref: str, dest_dir: str) -> str:
        return ""


class NoOpEvidenceUploader(EvidenceUploader):
    def upload(self, local_path: str, key: str) -> str:
        return ""
