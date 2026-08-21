"""Lắp ráp chain các handler."""
from __future__ import annotations

from .handlers import (
    CheckMissingHandler,
    DrawZoneHandler,
    Handler,
    HealthCheckHandler,
    LoadGroundTruthHandler,
    PublishEvidenceHandler,
    ReportHandler,
    TriggerHandler,
    WaitHandler,
    GetResultsHandler,
    UpdateRuleConfigHandler,
    UploadHandler,
    ValidateHandler,
)


def build_default_chain(include_health: bool = True) -> Handler:
    """Chain mặc định khớp flow dự án. Trả về handler đầu tiên.

    HealthCheck (nếu bật) chạy đầu tiên; hệ thống không khoẻ -> pipeline dừng ngay.
    """
    steps: list[Handler] = []
    if include_health:
        steps.append(HealthCheckHandler())
    steps += [
        LoadGroundTruthHandler(),
        CheckMissingHandler(),
        UploadHandler(),
        UpdateRuleConfigHandler(),   # /api/rule (generic API); hand-washing no-op
        TriggerHandler(),
        DrawZoneHandler(),  # vẽ ảnh debug zone cạnh video nguồn trên S3 (bỏ qua nếu đã có)
        WaitHandler(),
        GetResultsHandler(),
        ValidateHandler(),
        PublishEvidenceHandler(),    # fetch evidence -> S3 -> gắn link (chỉ khi xuất HTML)
        ReportHandler(),
    ]
    for a, b in zip(steps, steps[1:]):
        a.set_next(b)
    return steps[0]


def build_full_flow_chain(include_health: bool = True) -> Handler:
    steps: list[Handler] = []
    if include_health:
        steps.append(HealthCheckHandler())
    steps += [
        LoadGroundTruthHandler(),
        CheckMissingHandler(),
        # GenerateQRCodeIntoVideoHandler(),
        UploadHandler(),
        UpdateRuleConfigHandler(),  # /api/rule (generic API); hand-washing no-op
        TriggerHandler(),
        DrawZoneHandler(),  # vẽ ảnh debug zone cạnh video nguồn trên S3 (bỏ qua nếu đã có)
        WaitHandler(),
        GetResultsHandler(),
        ValidateHandler(),
        PublishEvidenceHandler(),  # fetch evidence -> S3 -> gắn link (chỉ khi xuất HTML)
        ReportHandler(),
    ]
    for a, b in zip(steps, steps[1:]):
        a.set_next(b)
    return steps[0]