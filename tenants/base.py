"""`TenantFactory` mặc định — tạo thành phần pipeline cho tenant không có override.

Subclass override các create_* (hoặc các hook class-level `http_client_cls`,
`default_sheet_columns`, `evidence_fetcher_cls`) rồi `@register("<load_dir>")`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from adapters import (
    FakeSheetGroundTruthSource,
    GoogleSheetGroundTruthSource,
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

from .options import RunOptions

if TYPE_CHECKING:  # tránh circular import (pipeline -> runner -> tenants)
    from adapters.evidence import FileBrowserFetcher
    from pipeline.handlers import Handler

log = logging.getLogger("ai_testing.factory")


class TenantFactory:
    """Factory mặc định. Override các create_* trong subclass nếu cần."""

    # Lớp HTTP client mặc định cho tenant (generic API). Subclass override nếu khác.
    http_client_cls = LinfoxHttpClient

    # Map cột Google Sheet thật (key nội bộ -> tên cột) — áp khi đọc gsheet/gsheet_public
    # mà người dùng không truyền `sheet_columns`. Rỗng = dùng mặc định của source.
    default_sheet_columns: ClassVar[dict[str, str]] = {}

    def http_client_kwargs(self, opts: RunOptions) -> dict:
        """Kwargs riêng cho HTTP client của tenant (mặc định không có). Override khi cần."""
        return {}

    def evidence_fetcher_cls(self) -> type["FileBrowserFetcher"]:
        """Lớp fetcher tải evidence từ File Browser cho report HTML. Override khi tenant
        có cách tải riêng (cùng constructor với `FileBrowserFetcher`)."""
        from adapters.evidence import FileBrowserFetcher

        return FileBrowserFetcher

    def create_client(self, tenant: TenantConfig, opts: RunOptions) -> AnalysisClient:
        if opts.analysis == "mock":
            return MockAnalysisClient(opts.far_rate, opts.miss_rate, opts.seed)
        if opts.analysis == "http":
            if not opts.api_url:
                raise ValueError("analysis=http cần env AGENT_API (gốc host API)")
            return self.http_client_cls(
                opts.api_url, opts.api_key, opts.poll_interval_sec, opts.timeout_sec,
                **self.http_client_kwargs(opts),
            )
        raise ValueError(f"analysis không hỗ trợ: {opts.analysis!r}")

    def create_ground_truth_source(
            self, tenant: TenantConfig, opts: RunOptions
    ) -> GroundTruthSource:
        if opts.source in ("gsheet", "gsheet_public") and not opts.sheet_columns:
            opts.sheet_columns = dict(self.default_sheet_columns)
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

        `--qr-validate`: chain = [binary như cũ, QRAlignmentStrategy] -> report có thêm layer
        "<rule> - QR Alignment" (evidence có đúng thời điểm vi phạm không).
        """
        if opts.qr_validate:
            from validator.qr_alignment_strategy import QRAlignmentStrategy
            from validator.strategies import BinaryViolationStrategy

            return [
                BinaryViolationStrategy(),
                QRAlignmentStrategy(fps=opts.qr_fps, tolerance_sec=opts.qr_tolerance_sec),
            ]
        return []

    def create_evidence_fetcher(self, tenant: TenantConfig, opts: RunOptions):
        """Fetcher tải clip evidence (đọc QR). None khi mock hoặc chưa cấu hình file-browser."""
        if opts.analysis == "mock" or not opts.file_browser_url:
            if opts.analysis != "mock":
                log.warning("Không đọc được QR evidence — thiếu env FILE_BROWSER_URL")
            return None
        from adapters.evidence import FileBrowserFetcher

        return _build_file_browser_fetcher(FileBrowserFetcher, opts)

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
                    missing.append("env FILE_BROWSER_URL")
                if not bucket:
                    missing.append("env S3_BUCKET / --report-s3-bucket")
                log.warning(
                    "Evidence sẽ KHÔNG publish được — thiếu: %s", ", ".join(missing)
                )
            return None
        from adapters.storage import S3PublicUploader
        from report.evidence import EvidencePublisher

        fetcher = _build_file_browser_fetcher(self.evidence_fetcher_cls(), opts)
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

        return build_default_chain(
            include_health=not opts.skip_health,
            qr_validate=opts.qr_validate,
            evidence_field=opts.evidence_field,
        )

    def create_waiting(self, tenant: TenantConfig, opts: RunOptions) -> WaitingBase:
        from adapters.waiting import WaitingTimeout

        if not opts.waiting_time:
            raise ValueError(
                f"Tenant {tenant.name!r} cần env TIME_WAITING (định dạng 'H:M:S', vd "
                "'0:30:0') để biết chờ AI xử lý bao lâu — tenant này không dùng "
                "Kafka/Grafana để tự dò tín hiệu rảnh."
            )
        try:
            hour, mut, sec = opts.waiting_time.split(":")
        except ValueError as exc:
            raise ValueError(
                f"TIME_WAITING={opts.waiting_time!r} sai định dạng, cần 'H:M:S' (vd '0:30:0')"
            ) from exc

        return WaitingTimeout(seconds=int(sec), minutes=int(mut), hours=int(hour))


def _build_file_browser_fetcher(cls: type["FileBrowserFetcher"], opts: RunOptions) -> "FileBrowserFetcher":
    return cls(
        base_url=opts.file_browser_url,
        user=opts.file_browser_user,
        password=opts.file_browser_password,
        timeout_sec=opts.timeout_sec,
    )
