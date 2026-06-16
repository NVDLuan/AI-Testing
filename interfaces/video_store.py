"""Interface nơi lưu video nguồn (S3, local...) để tải về trước khi upload."""
from __future__ import annotations

from abc import ABC, abstractmethod

from validator.models import VideoRef


class VideoStore(ABC):
    @abstractmethod
    def download(self, video: VideoRef, dest_dir: str) -> str:
        """Tải video về `dest_dir`, trả về đường dẫn file local."""
        raise NotImplementedError
