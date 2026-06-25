"""Interface nơi lưu video nguồn (S3, local...) để lấy về rồi upload."""
from __future__ import annotations

from abc import ABC, abstractmethod

from validator.models import VideoRef


class VideoStore(ABC):
    @abstractmethod
    def fetch(self, video: VideoRef) -> tuple[bytes, str]:
        """Lấy video vào RAM. Trả (nội dung bytes, tên file) để upload thẳng, khỏi ghi đĩa."""
        raise NotImplementedError
