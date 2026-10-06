"""Các handler của pipeline theo mẫu Chain of Responsibility.

Mỗi handler làm đúng MỘT việc rồi chuyển context cho handler kế tiếp:

    HealthCheck -> LoadGroundTruth -> CheckMissing -> Upload -> UpdateRuleConfig -> Trigger
        -> DrawZone -> Wait -> GetResults -> (ReadEvidenceQR) -> Validate
        -> PublishEvidence -> Report

Thêm/bớt/đổi thứ tự bước = sửa danh sách trong chain.build_default_chain().
Mỗi nhóm bước 1 module: base · health · ingest · analysis · draw_zone · evidence_qr ·
validation · publish_evidence · reporting.
"""
from .analysis import GetResultsHandler, TriggerHandler, UpdateRuleConfigHandler, WaitHandler
from .base import Handler
from .draw_zone import DrawZoneHandler
from .evidence_qr import ReadEvidenceQRHandler
from .health import HealthCheckHandler
from .ingest import CheckMissingHandler, LoadGroundTruthHandler, UploadHandler
from .publish_evidence import PublishEvidenceHandler
from .reporting import ReportHandler
from .validation import ValidateHandler

__all__ = [
    "CheckMissingHandler", "DrawZoneHandler", "GetResultsHandler", "Handler", "HealthCheckHandler",
    "LoadGroundTruthHandler", "PublishEvidenceHandler", "ReadEvidenceQRHandler", "ReportHandler",
    "TriggerHandler", "UpdateRuleConfigHandler", "UploadHandler", "ValidateHandler", "WaitHandler",
]
