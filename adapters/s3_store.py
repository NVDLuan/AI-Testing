"""S3: tải video nguồn về (S3VideoStore) và đẩy evidence công khai lên (S3PublicUploader).

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

from interfaces.evidence import EvidenceUploader
from interfaces.video_store import VideoStore
from validator.models import VideoRef

log = logging.getLogger("ai_testing.s3")


def make_s3_client(
    region: str = "",
    access_key: str = "",
    secret_key: str = "",
    endpoint_url: str = "",
    addressing_style: str = "",
):
    """Tạo boto3 S3 client (import + validate key lười). Dùng chung cho download/upload.

    `addressing_style`: 'path' cho S3-compatible như MinIO (bucket vào path, không phải
    subdomain). Để trống = mặc định của boto3 (virtual-hosted cho AWS).
    """
    try:
        import boto3  # noqa: WPS433
        from botocore.config import Config  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Cần cài boto3 để dùng S3") from exc
    if not (access_key and secret_key):
        raise ValueError("S3 cần access key + secret key (--s3-access-key/--s3-secret-key)")
    config = Config(s3={"addressing_style": addressing_style}) if addressing_style else None
    return boto3.client(
        "s3",
        region_name=region or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint_url or None,
        config=config,
    )


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


def endpoint_for_url(url: str) -> tuple[str, str]:
    """Suy (endpoint_url, addressing_style) cho S3 API từ host của một URL http(s).

        AWS (…amazonaws.com) -> ("", "")        : để boto3 tự dựng theo region (virtual)
        host khác (MinIO...)  -> ("scheme://host", "path")
    """
    p = urlparse(url)
    if not p.netloc or p.netloc.endswith("amazonaws.com"):
        return "", ""
    return f"{p.scheme}://{p.netloc}", "path"


class NoOpVideoStore(VideoStore):
    """Không tải gì — dùng cho mock/offline."""

    def download(self, video: VideoRef, dest_dir: str) -> str:
        return ""


class S3VideoStore(VideoStore):
    """Tải video nguồn, tự định tuyến theo từng URL:

    1. URL http(s): thử tải PUBLIC qua HTTP trước (nhiều bucket để public-read, vd AWS
       rainscales-static-assets) -> khỏi cần creds.
    2. Nếu HTTP bị 401/403 (bucket riêng tư, vd MinIO): fallback **S3 API**, endpoint +
       path-style suy từ host của chính URL đó (không dùng endpoint cố định trong .env).
    3. `s3://bucket/key` hoặc key thuần: dùng bucket/endpoint cấu hình sẵn.
    """

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
        self._clients: dict[tuple[str, str], object] = {}  # cache theo (endpoint, style)

    def _client(self, endpoint: str, style: str):
        cache_key = (endpoint, style)
        if cache_key not in self._clients:
            self._clients[cache_key] = make_s3_client(
                self._region, self._access_key, self._secret_key, endpoint, style
            )
        return self._clients[cache_key]

    @staticmethod
    def _http_download(url: str, local: str) -> bool:
        """Tải public qua HTTP. True nếu xong; False nếu 401/403/404 (để fallback S3)."""
        import requests  # noqa: WPS433

        try:
            with requests.get(url, stream=True, timeout=120) as r:
                if r.status_code in (401, 403, 404):
                    log.info("HTTP public %s -> %s, chuyển sang S3 API", r.status_code, url)
                    return False
                r.raise_for_status()
                with open(local, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):  # 1MB
                        if chunk:
                            f.write(chunk)
            return True
        except requests.RequestException as exc:
            log.warning("HTTP tải lỗi (%s), thử S3 API: %s", exc, url)
            return False

    def download(self, video: VideoRef, dest_dir: str) -> str:
        url = video.url
        bucket, key = parse_s3_url(url, self._bucket, self._prefix)
        if not bucket or not key:
            raise ValueError(f"Không phân giải được S3 từ url: {url!r}")
        os.makedirs(dest_dir, exist_ok=True)
        local = os.path.join(dest_dir, os.path.basename(key) or f"{video.name or video.video_id}.mp4")

        is_http = url.startswith("http://") or url.startswith("https://")
        if is_http:
            log.info("HTTP download %s -> %s", url, local)
            if self._http_download(url, local):
                return local
            endpoint, style = endpoint_for_url(url)  # bucket riêng tư -> S3 API theo host URL
        else:
            endpoint = self._endpoint_url
            style = "path" if (endpoint and "amazonaws.com" not in endpoint) else ""

        log.info("S3 API download s3://%s/%s (endpoint=%s) -> %s",
                 bucket, key, endpoint or "AWS", local)
        self._client(endpoint, style).download_file(bucket, key, local)
        return local


class S3PublicUploader(EvidenceUploader):
    """Đẩy video evidence lên S3 (public-read) để nhúng link vào report HTML.

    URL công khai dựng theo:
        - có `public_base`         -> f"{public_base}/{key}"  (CloudFront/domain riêng)
        - có `endpoint_url` (MinIO)-> f"{endpoint}/{bucket}/{key}"
        - còn lại (AWS S3)         -> f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    Bucket phải cho phép object public-read (ACL/bucket policy). Client tạo lười.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "",
        access_key: str = "",
        secret_key: str = "",
        endpoint_url: str = "",
        public_base: str = "",
        acl: str = "public-read",
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._endpoint_url = endpoint_url
        self._public_base = public_base.rstrip("/")
        self._acl = acl
        self._s3 = None

    def _client(self):
        if self._s3 is None:
            if not self._bucket:
                raise ValueError("S3PublicUploader cần bucket")
            self._s3 = make_s3_client(
                self._region, self._access_key, self._secret_key, self._endpoint_url
            )
        return self._s3

    def _public_url(self, key: str) -> str:
        if self._public_base:
            return f"{self._public_base}/{key}"
        if self._endpoint_url:
            return f"{self._endpoint_url.rstrip('/')}/{self._bucket}/{key}"
        region = self._region or "ap-southeast-1"
        return f"https://{self._bucket}.s3.{region}.amazonaws.com/{key}"

    def upload(self, local_path: str, key: str) -> str:
        extra = {"ACL": self._acl, "ContentType": "video/mp4"}
        log.info("S3 upload %s -> s3://%s/%s", local_path, self._bucket, key)
        self._client().upload_file(local_path, self._bucket, key, ExtraArgs=extra)
        return self._public_url(key)
