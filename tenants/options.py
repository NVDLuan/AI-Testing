"""`RunOptions` — tham số chạy (CLI/.env) truyền cho mọi factory."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunOptions:
    analysis: str = "mock"  # mock | http
    source: str = "fake"  # fake | gsheet
    api_url: str = ""
    api_key: str = ""
    credentials: str = ""
    poll_interval_sec: float = 5.0
    timeout_sec: float = 600.0
    far_rate: float = 0.15
    miss_rate: float = 0.10
    seed: int = 7
    skip_health: bool = False
    s3_bucket: str = ""
    s3_region: str = ""
    s3_prefix: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_endpoint: str = ""
    cache_dir: str = ""  # rỗng = giữ RAM; có giá trị = lưu video xuống đĩa + tái dùng
    # Chấm thời điểm vi phạm bằng QR trong clip evidence (ReadEvidenceQRHandler + QRAlignmentStrategy)
    qr_validate: bool = False
    qr_fps: float = 25.0  # fps video nguồn để đổi ExpectedResult (MM:SS) <-> frame_id trong QR
    qr_tolerance_sec: float = 1.0  # dung sai khi so khoảng QR evidence với khoảng vi phạm
    recordings_dir: str = ""
    # bv_c: danh sách layer (validate strategy) phân tách bởi dấu phẩy, xem
    # BvCFactory.create_validation_chain — trigger | violation | sequence | sequence_exact | count.
    hw_layers: str = "trigger,violation,sequence_exact,count"
    sheet_columns: dict[str, str] = field(default_factory=dict)
    # Evidence -> S3 -> report HTML
    file_browser_url: str = ""
    file_browser_user: str = ""
    file_browser_password: str = ""
    evidence_field: str = "evidence"
    report_s3_bucket: str = ""  # mặc định tái dùng s3_bucket
    report_s3_prefix: str = "reports"  # prefix key evidence trên S3
    s3_public_base: str = ""  # domain/CloudFront công khai (tuỳ chọn)
    waiting_time: str = ""
    kafka_watcher_api_url: str = ""
    # Chờ AI rảnh qua log Loki (Grafana) — dùng cho ews_usa, xem WaitingGrafana.
    grafana_url: str = ""
    grafana_username: str = ""
    grafana_password: str = ""
    grafana_server_name: str = ""
    grafana_service_name: str = ""
    grafana_target_logs: str = ""  # phân tách dấu phẩy, rỗng = EwsUsaFactory tự áp mặc định
    grafana_poll_interval_sec: float = 120.0
