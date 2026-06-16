"""Tải video từ S3 (hoặc no-op cho chạy offline).

Hỗ trợ URL dạng:
    s3://bucket/key
    https://bucket.s3.region.amazonaws.com/key   (virtual-hosted)
    https://s3.region.amazonaws.com/bucket/key    (path-style)
    key/thuần            -> dùng bucket + prefix mặc định
Credentials lấy theo chuỗi mặc định của boto3 (env AWS_*, profile, IAM role...).
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from interfaces.video_store import VideoStore
from validator.models import VideoRef

log = logging.getLogger("ai_testing.s3")


def parse_s3_url(url: str, default_bucket: str = "", prefix: str = "") -> tuple[str, str]:
    """Phân giải URL/key -> (bucket, key)."""
    if url.startswith("s3://"):
        bucket, _, key = url[5:].partition("/")
        return bucket, key
    if url.startswith("http://") or url.startswith("https://"):
        p = urlparse(url)
        host, key = p.netloc, p.path.lstrip("/")
        if ".s3" in host and not host.startswith("s3."):
            return host.split(".s3")[0], key  # virtual-hosted
        bucket, _, rest = key.partition("/")  # path-style
        return bucket, rest
    key = f"{prefix.rstrip('/')}/{url}" if prefix else url  # key thuần
    return default_bucket, key


class NoOpVideoStore(VideoStore):
    """Không tải gì — dùng cho mock/offline."""

    def download(self, video: VideoRef, dest_dir: str) -> str:
        return ""


class S3VideoStore(VideoStore):
    def __init__(
        self,
        bucket: str = "",
        region: str = "",
        prefix: str = "",
        access_key: str = "",
        secret_key: str = "",
        endpoint_url: str = "",
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._endpoint_url = endpoint_url
        self._s3 = None  # tạo lười: chỉ khởi tạo + validate key khi thực sự download

    def _client(self):
        if self._s3 is None:
            try:
                import boto3  # noqa: WPS433
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("Cần cài boto3 để tải video từ S3") from exc
            if not (self._access_key and self._secret_key):
                raise ValueError("S3 cần access key + secret key (--s3-access-key/--s3-secret-key)")
            self._s3 = boto3.client(
                "s3",
                region_name=self._region or None,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                endpoint_url=self._endpoint_url or None,
            )
        return self._s3

    def download(self, video: VideoRef, dest_dir: str) -> str:
        bucket, key = parse_s3_url(video.url, self._bucket, self._prefix)
        if not bucket or not key:
            raise ValueError(f"Không phân giải được S3 từ url: {video.url!r}")
        os.makedirs(dest_dir, exist_ok=True)
        local = os.path.join(dest_dir, os.path.basename(key) or f"{video.name or video.video_id}.mp4")
        log.info("S3 download s3://%s/%s -> %s", bucket, key, local)
        self._client().download_file(bucket, key, local)
        return local
