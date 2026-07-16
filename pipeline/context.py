"""Context dùng chung, được truyền qua từng handler trong chain."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from adapters.waiting import WaitingBase
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
    # Rỗng = validator tự chọn 1 strategy theo dữ liệu (default_strategy), không suffix
    # tên rule. >1 phần tử = mỗi strategy 1 report riêng, report coi là 1 "rule ảo"
    # "<rule> - <strategy.name>" (xem ValidateHandler + report/template.py).
    strategy_chain: list[ValidationStrategy] = field(default_factory=list)
    evidence_publisher: EvidencePublisher | None = None  # None = không publish evidence
    waiting: WaitingBase = None
    # Tích luỹ qua các bước
    ground_truth: list[GroundTruthRecord] = field(default_factory=list)
    videos_by_rule: dict[str, list[VideoRef]] = field(default_factory=dict)
    missing: list[VideoRef] = field(default_factory=list)
    predictions: dict[str, Prediction] = field(default_factory=dict)
    reports: dict[str, ValidationReport] = field(default_factory=dict)  # key = strategy.name
    batch_codes: dict[str, str] = field(default_factory=dict)  # rule.code -> batch_code (trigger)
    rule_api_response: dict[str, dict] = field(default_factory=dict)  # rule.code -> phản hồi /api/rule
    started_at: float = 0.0  # epoch lúc bắt đầu run (cho timing trong report template)

    # Điều khiển chain
    aborted: bool = False
    abort_reason: str = ""
    health: HealthStatus | None = None

    log: logging.Logger = field(default_factory=lambda: logging.getLogger("ai_testing.pipeline"))

    def abort(self, reason: str) -> None:
        self.aborted = True
        self.abort_reason = reason
