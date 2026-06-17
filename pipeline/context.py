"""Context dùng chung, được truyền qua từng handler trong chain."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from configs.loader import ConfigLoader
from configs.schema import JobDef, RuleDef, TenantConfig
from interfaces.analysis_client import AnalysisClient, HealthStatus
from interfaces.ground_truth import GroundTruthSource
from interfaces.video_store import VideoStore
from report.evidence import EvidencePublisher
from validator.models import GroundTruthRecord, Prediction, VideoRef
from validator.strategies import ValidationStrategy
from validator.validator import ValidationReport


@dataclass
class PipelineContext:
    # Đầu vào / dịch vụ (factory bơm vào)
    loader: ConfigLoader
    tenant: TenantConfig
    job: JobDef
    rules: list[RuleDef]
    client: AnalysisClient
    gt_source: GroundTruthSource
    video_store: VideoStore
    report_formats: list[str] = field(default_factory=lambda: ["console"])
    report_dir: str = "reports"
    download_dir: str = "downloads"
    strategy: ValidationStrategy | None = None  # None = validator tự chọn theo dữ liệu
    evidence_publisher: EvidencePublisher | None = None  # None = không publish evidence

    # Tích luỹ qua các bước
    ground_truth: list[GroundTruthRecord] = field(default_factory=list)
    videos_by_rule: dict[str, list[VideoRef]] = field(default_factory=dict)
    missing: list[VideoRef] = field(default_factory=list)
    predictions: dict[str, Prediction] = field(default_factory=dict)
    report: ValidationReport | None = None

    # Điều khiển chain
    aborted: bool = False
    abort_reason: str = ""
    health: HealthStatus | None = None

    log: logging.Logger = field(default_factory=lambda: logging.getLogger("ai_testing.pipeline"))

    def abort(self, reason: str) -> None:
        self.aborted = True
        self.abort_reason = reason
