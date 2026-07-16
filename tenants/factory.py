"""Factory tạo thành phần pipeline cho từng tenant.

Mặc định mọi tenant dùng `TenantFactory`. Khi một khách hàng cần hành vi riêng
(API khác, cột sheet khác, chain khác...), tạo subclass và `@register("<load_dir>")`.
Runner chỉ gọi `get_factory(tenant)` — không cần biết tenant nào đặc biệt.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from adapters import (
    FakeSheetGroundTruthSource,
    GoogleSheetGroundTruthSource,
    HandWashingHttpClient,
    LinfoxHttpClient,
    MockAnalysisClient,
    NoOpVideoStore,
    PublicGoogleSheetGroundTruthSource,
    S3VideoStore,
)
from adapters.waiting import WaitingBase
from configs.schema import TenantConfig
from interfaces.analysis_client import AnalysisClient
from interfaces.ground_truth import GroundTruthSource
from interfaces.video_store import VideoStore
from validator.strategies import ValidationStrategy

if TYPE_CHECKING:  # tránh circular import (pipeline -> runner -> tenants.factory)
    from pipeline.handlers import Handler

log = logging.getLogger("ai_testing.factory")


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
    recordings_dir: str = ""
    # bv_c: danh sách layer (validate strategy) phân tách bởi dấu phẩy, xem
    # BvCFactory.create_validation_chain — trigger | violation | sequence | sequence_exact | count.
    hw_layers: str = "trigger,violation,sequence_exact,count"
    sheet_columns: dict[str, str] = field(default_factory=dict)
    # Evidence -> S3 -> report HTML
    file_browser_url: str = ""
    file_browser_user: str = ""
    file_browser_password: str = ""
    evidence_field: str = "evidence_path"
    report_s3_bucket: str = ""  # mặc định tái dùng s3_bucket
    report_s3_prefix: str = "reports/evidence"
    s3_public_base: str = ""  # domain/CloudFront công khai (tuỳ chọn)
    waiting_time: str = ""
    kafka_watcher_api_url: str = ""


class TenantFactory:
    """Factory mặc định. Override các create_* trong subclass nếu cần."""

    # Lớp HTTP client mặc định cho tenant (generic API). Subclass override nếu khác.
    http_client_cls = LinfoxHttpClient

    def http_client_kwargs(self, opts: RunOptions) -> dict:
        """Kwargs riêng cho HTTP client của tenant (mặc định không có). Override khi cần."""
        return {}

    def create_client(self, tenant: TenantConfig, opts: RunOptions) -> AnalysisClient:
        if opts.analysis == "mock":
            return MockAnalysisClient(opts.far_rate, opts.miss_rate, opts.seed)
        if opts.analysis == "http":
            if not opts.api_url:
                raise ValueError("analysis=http cần --api-url")
            return self.http_client_cls(
                opts.api_url, opts.api_key, opts.poll_interval_sec, opts.timeout_sec,
                **self.http_client_kwargs(opts),
            )
        raise ValueError(f"analysis không hỗ trợ: {opts.analysis!r}")

    def create_ground_truth_source(
            self, tenant: TenantConfig, opts: RunOptions
    ) -> GroundTruthSource:
        if opts.source == "fake":
            return FakeSheetGroundTruthSource(seed=opts.seed)
        if opts.source == "gsheet":
            return GoogleSheetGroundTruthSource(
                credentials_file=opts.credentials,
                columns=opts.sheet_columns or None,
            )
        if opts.source == "gsheet_public":
            return PublicGoogleSheetGroundTruthSource(columns=opts.sheet_columns or None)
        raise ValueError(f"source không hỗ trợ: {opts.source!r}")

    def create_video_store(self, tenant: TenantConfig, opts: RunOptions) -> VideoStore:
        # Offline/mock không tải gì; còn lại tải từ S3.
        if opts.analysis == "mock":
            return NoOpVideoStore()
        return S3VideoStore(
            bucket=opts.s3_bucket,
            region=opts.s3_region,
            prefix=opts.s3_prefix,
            access_key=opts.s3_access_key,
            secret_key=opts.s3_secret_key,
            endpoint_url=opts.s3_endpoint,
            cache_dir=opts.cache_dir,
        )

    def create_validation_chain(
            self, tenant: TenantConfig, opts: RunOptions
    ) -> list[ValidationStrategy]:
        """Chain luật chấm điểm cho tenant — mỗi phần tử chạy `validate()` riêng, sinh

        1 report riêng (report coi mỗi report là 1 "rule ảo" `<rule> - <strategy.name>`).
        Rỗng = để pipeline tự chọn 1 strategy duy nhất theo dữ liệu (`default_strategy`,
        binary nếu nhãn boolean, 3-state nếu có `expected_triggered`) — rule không bị
        đổi tên/suffix, giữ nguyên hành vi hiện tại (linfox/EWS).

        Override khi cần cách chấm riêng (vd nhiều tiêu chí như bv_c): trả về chain cụ thể.
        """
        return []

    def create_evidence_publisher(self, tenant: TenantConfig, opts: RunOptions):
        """Dựng EvidencePublisher (file-browser fetch -> S3 public upload) cho report HTML.

        Trả None khi: chạy mock/offline, hoặc chưa cấu hình file-browser/bucket -> report
        HTML vẫn ra nhưng không có link evidence.
        """
        if opts.analysis == "mock":
            return None
        bucket = opts.report_s3_bucket or opts.s3_bucket
        if not opts.file_browser_url or not bucket:
            # Báo rõ thiếu gì NẾU người dùng đang định dùng evidence (đã set 1 trong 2).
            if opts.file_browser_url or bucket:
                missing = []
                if not opts.file_browser_url:
                    missing.append("--file-browser-url (hoặc env FILE_BROWSER_URL)")
                if not bucket:
                    missing.append("--s3-bucket / --report-s3-bucket")
                log.warning(
                    "Evidence sẽ KHÔNG publish được — thiếu: %s", ", ".join(missing)
                )
            return None
        from adapters.file_browser import FileBrowserFetcher
        from adapters.s3_store import S3PublicUploader
        from report.evidence import EvidencePublisher

        fetcher = FileBrowserFetcher(
            base_url=opts.file_browser_url,
            user=opts.file_browser_user,
            password=opts.file_browser_password,
            timeout_sec=opts.timeout_sec,
        )
        uploader = S3PublicUploader(
            bucket=bucket,
            region=opts.s3_region,
            access_key=opts.s3_access_key,
            secret_key=opts.s3_secret_key,
            endpoint_url=opts.s3_endpoint,
            public_base=opts.s3_public_base,
        )
        return EvidencePublisher(
            fetcher, uploader,
            evidence_field=opts.evidence_field,
            key_prefix=opts.report_s3_prefix,
        )

    def create_chain(self, tenant: TenantConfig, opts: RunOptions) -> "Handler":
        from pipeline.chain import build_default_chain  # import lười, tránh vòng

        return build_default_chain(include_health=not opts.skip_health)

    def create_waiting(self, tenant: TenantConfig, opts: RunOptions) -> WaitingBase:
        from adapters.waiting import WaitingTimeout
        hour, mut, sec = opts.waiting_time.split(":")

        return WaitingTimeout(seconds=int(sec), minutes=int(mut), hours=int(hour))


# --- Registry override theo load_dir ---------------------------------------

_REGISTRY: dict[str, type[TenantFactory]] = {}


def register(load_dir: str) -> Callable[[type[TenantFactory]], type[TenantFactory]]:
    def deco(cls: type[TenantFactory]) -> type[TenantFactory]:
        _REGISTRY[load_dir] = cls
        return cls

    return deco


def get_factory(tenant: TenantConfig) -> TenantFactory:
    return _REGISTRY.get(tenant.load_dir, TenantFactory)()


# --- Ví dụ override cho một tenant ------------------------------------------


@register("linfox_vn")
class LinfoxVNFactory(TenantFactory):
    """Linfox VN: generic API + map cột sheet thật khi đọc Google Sheet."""

    http_client_cls = LinfoxHttpClient

    def create_ground_truth_source(
            self, tenant: TenantConfig, opts: RunOptions
    ) -> GroundTruthSource:
        if opts.source in ("gsheet", "gsheet_public") and not opts.sheet_columns:
            opts.sheet_columns = {
                "video_url": "Video Raw",  # tạm dùng, chưa có cột path video riêng
                "video_name": "Video Name",
                "expected": "Expected Status",  # "Approve" = có vi phạm
                "camera": "Camera Code",
            }
        return super().create_ground_truth_source(tenant, opts)

    def create_waiting(self, tenant: TenantConfig, opts: RunOptions) -> WaitingBase:
        from adapters.waiting import WaitingKafka
        return WaitingKafka(opts.kafka_watcher_api_url)


@register("bv_c")
class BvCFactory(TenantFactory):
    """BV C: dùng API hand-washing (/api/hand-washing/*) + map cột sheet riêng.

    Sheet thật có cột "Video Name" (kèm đuôi .mp4), "Video Raw" (URL S3 — tạm dùng
    thay cho video_path, sẽ có cột riêng sau), "Expected Status" ("Trigger"/"No trigger")
    và "camera_code" (khớp với camera_id gửi cho API trigger).
    """

    http_client_cls = HandWashingHttpClient

    def http_client_kwargs(self, opts: RunOptions) -> dict:
        # Upload hand-washing cần kèm file keypoints .txt -> trỏ thư mục recordings.
        return {"recordings_dir": opts.recordings_dir}

    def create_validation_chain(self, tenant: TenantConfig, opts: RunOptions) -> list[ValidationStrategy]:
        # Chain layer theo --hw-layers (vd "trigger,violation,sequence,count"). Mỗi layer
        # chạy validate() riêng -> report riêng -> report ra 1 rule ảo "<rule> - <Name>".
        from validator.hand_washing_strategy import (
            CountStrategy, SequenceStrategy, TriggerStrategy, ViolationStrategy,
        )

        layer_factories: dict[str, Callable[[], ValidationStrategy]] = {
            "trigger": TriggerStrategy,
            "violation": ViolationStrategy,
            "sequence": lambda: SequenceStrategy(exact=False),
            "sequence_exact": lambda: SequenceStrategy(exact=True),
            "count": CountStrategy,
        }
        tokens = [t.strip().lower() for t in (opts.hw_layers or "").split(",") if t.strip()]
        unknown = [t for t in tokens if t not in layer_factories]
        if unknown:
            raise ValueError(
                f"--hw-layers có token không hợp lệ: {unknown!r} "
                f"(hợp lệ: {sorted(layer_factories)})"
            )
        return [layer_factories[t]() for t in tokens]

    def create_ground_truth_source(
            self, tenant: TenantConfig, opts: RunOptions
    ) -> GroundTruthSource:
        if opts.source in ("gsheet", "gsheet_public") and not opts.sheet_columns:
            opts.sheet_columns = {
                "video_url": "Video Raw",  # tạm dùng, chưa có cột path video riêng
                "video_name": "Video Name",
                "trigger_status": "Expected Status",
                # 3-state: No Trigger / Trigger (No Violation) / Trigger (Violation)
                "camera": "camera_code",
            }
        return super().create_ground_truth_source(tenant, opts)

    def create_evidence_publisher(self, tenant: TenantConfig, opts: RunOptions):
        """Dựng EvidencePublisher (file-browser fetch -> S3 public upload) cho report HTML.

        Trả None khi: chạy mock/offline, hoặc chưa cấu hình file-browser/bucket -> report
        HTML vẫn ra nhưng không có link evidence.
        """
        if opts.analysis == "mock":
            return None
        bucket = opts.report_s3_bucket or opts.s3_bucket
        if not opts.file_browser_url or not bucket:
            # Báo rõ thiếu gì NẾU người dùng đang định dùng evidence (đã set 1 trong 2).
            if opts.file_browser_url or bucket:
                missing = []
                if not opts.file_browser_url:
                    missing.append("--file-browser-url (hoặc env FILE_BROWSER_URL)")
                if not bucket:
                    missing.append("--s3-bucket / --report-s3-bucket")
                log.warning(
                    "Evidence sẽ KHÔNG publish được — thiếu: %s", ", ".join(missing)
                )
            return None
        from adapters.file_browser import DownloadEvidenceHandWashingFetcher
        from adapters.s3_store import S3PublicUploader
        from report.evidence import EvidencePublisher

        fetcher = DownloadEvidenceHandWashingFetcher(
            base_url=opts.file_browser_url,
            user=opts.file_browser_user,
            password=opts.file_browser_password,
            timeout_sec=opts.timeout_sec,
        )
        uploader = S3PublicUploader(
            bucket=bucket,
            region=opts.s3_region,
            access_key=opts.s3_access_key,
            secret_key=opts.s3_secret_key,
            endpoint_url=opts.s3_endpoint,
            public_base=opts.s3_public_base,
        )
        return EvidencePublisher(
            fetcher, uploader,
            evidence_field=opts.evidence_field,
            key_prefix=opts.report_s3_prefix,
        )
