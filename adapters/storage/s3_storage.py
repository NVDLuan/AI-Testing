"""S3 Storage Connector — upload/download evidence ảnh & video lên AWS S3 / MinIO.

Module tự chứa: nhận cấu hình qua constructor, fallback về biến môi trường khi bỏ
trống (cùng tên với `.env.example`: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
S3_BUCKET / AWS_DEFAULT_REGION / S3_ENDPOINT / S3_PUBLIC_BASE). KHÔNG phụ thuộc
config/Settings hay module GPU ngoài — ffmpeg convert chạy CPU (libx264).
"""
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import boto3
    from botocore.exceptions import ClientError as S3Error
except ImportError:
    boto3 = None
    S3Error = Exception

logger = logging.getLogger("ai_testing.s3.storage")


def _convert_to_h264(video_bytes: bytes) -> Optional[bytes]:
    """Convert bất kỳ video nào sang H.264 MP4 bằng ffmpeg (CPU libx264)."""
    input_path = output_path = None
    try:
        logger.info("Converting video to H.264 MP4 using ffmpeg (libx264)")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as input_tmp:
            input_tmp.write(video_bytes)
            input_path = input_tmp.name

        output_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        output_path = output_tmp.name
        output_tmp.close()

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            output_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="ignore"))

        with open(output_path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.error(f"ffmpeg convert failed: {e}")
        return None
    finally:
        if input_path and os.path.exists(input_path):
            os.remove(input_path)
        if output_path and os.path.exists(output_path):
            os.remove(output_path)


class S3StorageConnector:
    """Connector AWS S3 / MinIO để upload (và download) ảnh, video.

    Cấu hình truyền tường minh; bỏ trống thì lấy từ env. `bucket` có thể là
    'bucket' hoặc 'bucket/prefix'. `prefix` (nếu có) được tự ghép vào trước object key.
    """

    def __init__(
        self,
        bucket: str = "",
        region: str = "",
        access_key: str = "",
        secret_key: str = "",
        endpoint_url: str = "",
        prefix: str = "",
        public_base: str = "",
    ):
        if boto3 is None:
            raise ImportError("boto3 chưa cài. Cài bằng: pip install boto3")

        bucket_arg = bucket or os.getenv("S3_BUCKET", "")
        self.bucket_name, base_prefix = self._parse_bucket_and_prefix(bucket_arg)
        self.bucket_prefix = self._join_prefix(base_prefix, prefix)

        self.endpoint_url = (endpoint_url or os.getenv("S3_ENDPOINT", "")).strip() or None
        self.region = region or os.getenv("AWS_DEFAULT_REGION", "") or os.getenv("S3_REGION", "")
        self.access_key = access_key or os.getenv("AWS_ACCESS_KEY_ID", "")
        self.secret_key = secret_key or os.getenv("AWS_SECRET_ACCESS_KEY", "")
        self.public_base = (public_base or os.getenv("S3_PUBLIC_BASE", "")).rstrip("/")

        self.client = None
        self._connected = False

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _parse_bucket_and_prefix(s3_path: str) -> Tuple[Optional[str], str]:
        if not s3_path:
            return None, ""
        s3_path = s3_path.lstrip("/").strip()
        if not s3_path:
            return None, ""
        if "/" not in s3_path:
            return s3_path, ""
        bucket, prefix = s3_path.split("/", 1)
        return bucket, prefix.strip("/")

    @staticmethod
    def _join_prefix(*parts: str) -> str:
        cleaned = [str(p).strip("/").strip() for p in parts if p and str(p).strip("/").strip()]
        return "/".join(cleaned)

    def _key_with_prefix(self, object_name: str) -> str:
        object_name = object_name.lstrip("/")
        if not self.bucket_prefix:
            return object_name
        return f"{self.bucket_prefix}/{object_name}"

    def _put_kwargs(self, content_type: str) -> dict:
        return {"ContentType": content_type}

    def _get_object_url(self, object_name: str) -> str:
        """Dựng URL công khai cho object."""
        object_key = self._key_with_prefix(object_name.lstrip("/"))
        if self.public_base:
            return f"{self.public_base}/{object_key}"
        if self.endpoint_url:
            return f"{self.endpoint_url.rstrip('/')}/{self.bucket_name}/{object_key}"
        region = self.region or "ap-southeast-1"
        return f"https://{self.bucket_name}.s3.{region}.amazonaws.com/{object_key}"

    # --- connect ---------------------------------------------------------

    def connect(self) -> bool:
        """Kết nối S3 và kiểm tra bucket tồn tại."""
        try:
            if not self.bucket_name:
                raise ValueError("Thiếu bucket (truyền 'bucket'/'bucket/prefix' hoặc env S3_BUCKET)")
            if not self.access_key or not self.secret_key:
                raise ValueError("Thiếu access_key / secret_key (env AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)")

            # MinIO (endpoint non-AWS) cần path-style addressing.
            config = None
            if self.endpoint_url and "amazonaws.com" not in self.endpoint_url:
                from botocore.config import Config
                config = Config(s3={"addressing_style": "path"})

            self.client = boto3.client(
                "s3",
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region or None,
                endpoint_url=self.endpoint_url,
                config=config,
            )
            self.client.head_bucket(Bucket=self.bucket_name)
            self._connected = True
            logger.info(
                "Connected to S3: endpoint=%s bucket=%s prefix=%s",
                self.endpoint_url or "(AWS default)",
                self.bucket_name,
                self.bucket_prefix or "(none)",
            )
            return True
        except Exception as e:
            logger.error(f"S3 connect error: {e}")
            self._connected = False
            return False

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("Chưa kết nối. Gọi connect() trước.")

    # --- upload ----------------------------------------------------------

    def upload_image(
        self,
        image_data: bytes,
        object_name: str,
        content_type: str = "image/webp",
    ) -> Optional[str]:
        """Upload ảnh từ bytes. Trả URL công khai, None nếu lỗi."""
        self._ensure_connected()
        try:
            object_name = object_name.lstrip("/")
            object_key = self._key_with_prefix(object_name)
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=image_data,
                **self._put_kwargs(content_type),
            )
            url = self._get_object_url(object_name)
            logger.debug(f"Uploaded image: {object_key} ({len(image_data)} bytes)")
            return url
        except Exception as e:
            logger.error(f"Upload image error {object_name}: {e}")
            return None

    def upload_image_from_path(
        self,
        local_path: str,
        object_name: str,
        content_type: str = "image/webp",
    ) -> Optional[str]:
        path = Path(local_path)
        if not path.exists():
            logger.error(f"File not found: {local_path}")
            return None
        return self.upload_image(path.read_bytes(), object_name, content_type)

    def upload_video(
        self,
        video_data: bytes,
        object_name: str,
        content_type: str = "video/mp4",
        is_h264: bool = True,
    ) -> Optional[str]:
        """Upload video từ bytes (idempotent: bỏ qua nếu object đã tồn tại)."""
        self._ensure_connected()
        try:
            object_name = object_name.lstrip("/")
            object_key = self._key_with_prefix(object_name)
            try:
                self.client.head_object(Bucket=self.bucket_name, Key=object_key)
                logger.info(f"Video already exists on S3: {object_key}")
                return self._get_object_url(object_name)
            except S3Error:
                pass  # chưa có -> upload tiếp

            if not is_h264:
                converted = _convert_to_h264(video_data)
                if converted is not None:
                    video_data = converted

            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=video_data,
                **self._put_kwargs(content_type),
            )
            url = self._get_object_url(object_name)
            logger.info(f"Uploaded video: {object_name} ({len(video_data) / (1024 * 1024):.2f} MB)")
            return url
        except Exception as e:
            logger.error(f"Video upload error {object_name}: {e}")
            return None

    def upload_video_from_path(
        self,
        local_path: str,
        object_name: str,
        content_type: str = None,
        is_h264: bool = True,
    ) -> Optional[str]:
        """Upload video từ file local (auto-detect content type theo đuôi file)."""
        path = Path(local_path)
        if not path.exists():
            logger.error(f"Video file not found: {local_path}")
            return None
        if content_type is None:
            content_type = {
                ".mp4": "video/mp4",
                ".avi": "video/x-msvideo",
                ".mov": "video/quicktime",
                ".wmv": "video/x-ms-wmv",
                ".flv": "video/x-flv",
                ".webm": "video/webm",
                ".mkv": "video/x-matroska",
                ".m4v": "video/x-m4v",
            }.get(path.suffix.lower(), "video/mp4")
        return self.upload_video(path.read_bytes(), object_name, content_type, is_h264=is_h264)

    # --- misc ------------------------------------------------------------

    def get_object_url(self, object_name: str) -> str:
        return self._get_object_url(object_name)

    def object_exists(self, object_name: str) -> bool:
        self._ensure_connected()
        try:
            self.client.head_object(
                Bucket=self.bucket_name, Key=self._key_with_prefix(object_name.lstrip("/"))
            )
            return True
        except S3Error:
            return False
        except Exception as e:
            logger.error(f"Check exists error: {e}")
            return False

    def delete_object(self, object_name: str) -> bool:
        self._ensure_connected()
        try:
            self.client.delete_object(
                Bucket=self.bucket_name, Key=self._key_with_prefix(object_name.lstrip("/"))
            )
            logger.debug(f"Deleted object: {object_name}")
            return True
        except S3Error as e:
            logger.error(f"Delete error {object_name}: {e}")
            return False

    def get_video(self, object_name: str) -> Optional[bytes]:
        """Tải object về RAM."""
        self._ensure_connected()
        try:
            key = self._key_with_prefix(object_name.lstrip("/"))
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
            data = response["Body"].read()
            logger.info(f"Downloaded {key}: {len(data)} bytes")
            return data
        except Exception as e:
            logger.error(f"Download error {object_name}: {e}")
            return None

    def get_video_by_list(
        self, dir_path: str, list_video_names: List[str]
    ) -> List[Tuple[str, Optional[bytes]]]:
        self._ensure_connected()
        results = []
        for video_name in list_video_names:
            object_name = f"{dir_path}/{video_name}".lstrip("/")
            results.append((video_name, self.get_video(object_name)))
        return results

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        pass
