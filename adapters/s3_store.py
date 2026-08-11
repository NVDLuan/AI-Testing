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
from urllib.parse import unquote, urlparse

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
        # decode %20... -> key thật (object key S3/cache path không dùng percent-encoding)
        host, key = p.netloc, unquote(p.path.lstrip("/"))
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

    def fetch(self, video: VideoRef) -> tuple[bytes, str]:
        return b"", ""


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
        cache_dir: str = "",
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._endpoint_url = endpoint_url
        # cache_dir rỗng = chế độ RAM (không ghi đĩa). Có giá trị = lưu đĩa + tái dùng.
        self._cache_dir = cache_dir
        self._clients: dict = {}  # cache client boto3 theo (endpoint, style)

    def _client(self, endpoint: str, style: str):
        cache_key = (endpoint, style)
        if cache_key not in self._clients:
            self._clients[cache_key] = make_s3_client(
                self._region, self._access_key, self._secret_key, endpoint, style
            )
        return self._clients[cache_key]

    @staticmethod
    def _http_fetch(url: str) -> bytes | None:
        """Lấy nội dung qua HTTP public. Trả bytes; None nếu 401/403/404 (để fallback S3)."""
        import requests  # noqa: WPS433

        try:
            r = requests.get(url, timeout=120)
            if r.status_code in (401, 403, 404):
                log.info("HTTP public %s -> %s, chuyển sang S3 API", r.status_code, url)
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            log.warning("HTTP tải lỗi (%s), thử S3 API: %s", exc, url)
            return None

    def _endpoint_for(self, url: str) -> tuple[str, str]:
        """(endpoint_url, addressing_style) cho 1 url/key — cùng quy tắc dùng ở fetch()."""
        if url.startswith("http://") or url.startswith("https://"):
            return endpoint_for_url(url)  # bucket riêng tư -> S3 API theo host URL
        endpoint = self._endpoint_url
        return endpoint, ("path" if (endpoint and "amazonaws.com" not in endpoint) else "")

    def _download_bytes(self, url: str, bucket: str, key: str) -> bytes:
        """Tải nội dung video vào RAM: HTTP public trước, fallback S3 API theo host."""
        is_http = url.startswith("http://") or url.startswith("https://")
        if is_http:
            log.info("HTTP fetch %s", url)
            data = self._http_fetch(url)
            if data is not None:
                return data
        endpoint, style = self._endpoint_for(url)

        import io  # noqa: WPS433

        log.info("S3 API fetch s3://%s/%s (endpoint=%s)", bucket, key, endpoint or "AWS")
        buf = io.BytesIO()
        self._client(endpoint, style).download_fileobj(bucket, key, buf)
        return buf.getvalue()

    def fetch(self, video: VideoRef) -> tuple[bytes, str]:
        url = video.url
        bucket, key = parse_s3_url(url, self._bucket, self._prefix)
        if not bucket or not key:
            raise ValueError(f"Không phân giải được S3 từ url: {url!r}")
        filename = os.path.basename(key) or f"{video.name or video.video_id}.mp4"

        # Chế độ lưu đĩa: dùng lại file đã tải nếu còn -> khỏi tải lại.
        cache_path = os.path.join(self._cache_dir, bucket, key) if self._cache_dir else ""
        if cache_path and os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            log.info("cache hit (bỏ qua tải): %s", cache_path)
            with open(cache_path, "rb") as f:
                return f.read(), filename

        data = self._download_bytes(url, bucket, key)

        if cache_path:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(data)
            log.info("đã lưu cache: %s", cache_path)
        return data, filename

    def object_exists(self, url: str) -> bool:
        """True nếu object tại url/key (cùng bucket suy ra như fetch()) đã tồn tại.

        Dùng để DrawZoneHandler bỏ qua vẽ+upload lại ảnh debug zone nếu đã có sẵn.
        """
        bucket, key = parse_s3_url(url, self._bucket, self._prefix)
        if not bucket or not key:
            return False
        endpoint, style = self._endpoint_for(url)
        try:
            self._client(endpoint, style).head_object(Bucket=bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 — không tồn tại HOẶC lỗi truy vấn -> coi như chưa có
            return False

    def upload_bytes(self, url: str, data: bytes, content_type: str) -> None:
        """Upload bytes lên đúng bucket/key suy từ url/key (cùng cơ chế resolve với fetch()).

        Dùng cho artefact phụ đặt CẠNH video nguồn (vd ảnh debug zone) — không phải
        evidence (đã có `S3PublicUploader` riêng, khác bucket/quy ước).
        """
        bucket, key = parse_s3_url(url, self._bucket, self._prefix)
        if not bucket or not key:
            raise ValueError(f"Không phân giải được S3 từ url: {url!r}")
        endpoint, style = self._endpoint_for(url)
        self._client(endpoint, style).put_object(
            Bucket=bucket, Key=key, Body=data, ContentType=content_type,
        )


class S3PublicUploader(EvidenceUploader):
    """Đẩy video evidence lên S3 (public-read) để nhúng link vào report HTML.

    Uỷ quyền cho `S3StorageConnector` (adapters/storage) — phần upload đã kiểm thử:
    idempotent (bỏ qua nếu object đã tồn tại) + dựng URL công khai theo public_base /
    endpoint (MinIO) / pattern AWS. Bucket phải cho phép object public-read. Connector
    tạo + connect() lười ở lần upload đầu.
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
        self._public_base = public_base
        self._acl = acl
        self._conn = None

    def _connector(self):
        if self._conn is None:
            if not self._bucket:
                raise ValueError("S3PublicUploader cần bucket")
            from adapters.storage.s3_storage import S3StorageConnector

            conn = S3StorageConnector(
                bucket=self._bucket,
                region=self._region,
                access_key=self._access_key,
                secret_key=self._secret_key,
                endpoint_url=self._endpoint_url,
                public_base=self._public_base,
            )
            if not conn.connect():
                raise RuntimeError(f"Không kết nối được S3 bucket {self._bucket!r}")
            self._conn = conn
        return self._conn

    def upload(self, content: bytes, filename: str, key: str, prefix: str = 'qc-automation') -> str:
        """Upload thẳng từ bytes trong RAM (không qua file tạm trên đĩa)."""
        from adapters.storage.s3_storage import guess_video_content_type

        log.info("S3 upload %s (%d bytes) -> s3://%s/%s", filename, len(content), self._bucket, key)
        # key đã gồm prefix đầy đủ (report_s3_prefix) -> connector không thêm prefix nữa.
        url = self._connector().upload_video(
            content, f'{prefix}/{key}', guess_video_content_type(filename)
        )
        if not url:
            raise RuntimeError(f"Upload S3 thất bại: {key}")
        return url
