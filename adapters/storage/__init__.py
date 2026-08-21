"""Storage connectors (S3 / MinIO) + VideoStore/EvidenceUploader adapters dựng trên đó."""
from __future__ import annotations

from .s3_storage import S3StorageConnector, guess_video_content_type
from .video_store import (
    NoOpVideoStore,
    S3PublicUploader,
    S3VideoStore,
    endpoint_for_url,
    make_s3_client,
    parse_s3_url,
)

__all__ = [
    "S3StorageConnector",
    "guess_video_content_type",
    "NoOpVideoStore",
    "S3PublicUploader",
    "S3VideoStore",
    "endpoint_for_url",
    "make_s3_client",
    "parse_s3_url",
]
