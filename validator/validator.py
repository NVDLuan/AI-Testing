"""So khớp prediction với ground truth -> kết quả + metrics.

Khung CHẤM ĐIỂM cố định ở đây; LUẬT so khớp tách ra `strategies.py` (cắm được):
    - duyệt từng video, gọi `strategy.classify(gt, pred)` -> outcome,
    - gom ConfusionMatrix tổng (overall) + theo rule + theo camera,
    - thu Comparison (kèm nhãn hiển thị + detail tuỳ luật) và danh sách MISSING.

Mặc định luật tự chọn theo dữ liệu (`default_strategy`): binary (linfox) hoặc 3-state
(bv_c). Muốn cách chấm khác (vd verify detection) -> truyền `strategy` riêng vào
`validate()` (factory tenant bơm qua pipeline).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .metrics import ConfusionMatrix
from .models import GroundTruthRecord, Prediction
from .strategies import ValidationStrategy, default_strategy


@dataclass
class Comparison:
    video_id: str
    video_url: str
    expected: bool | str          # nhãn kỳ vọng (bool binary | chuỗi trạng thái)
    predicted: bool | str | None  # None = không có kết quả phân tích
    outcome: str                  # TP/FP/FN/TN/MISSING (hoặc mã riêng của luật)
    rule_id: str = ""
    camera_id: str = ""
    confidence: float | None = None
    detail: dict[str, Any] | None = None  # thông tin phụ tuỳ luật (vd object/zone/IoU)
    event_index: int = 0          # thứ tự event trong video khi 1 video > 1 outcome
    event_count: int = 1          # tổng số outcome (event) mà video này sinh ra


@dataclass
class ValidationReport:
    overall: ConfusionMatrix
    by_rule: dict[str, ConfusionMatrix]
    by_camera: dict[str, ConfusionMatrix]
    comparisons: list[Comparison]
    missing: list[Comparison] = field(default_factory=list)
    strategy: str = ""  # tên luật đã dùng (để báo cáo biết chấm theo cách nào)


def validate(
    ground_truth: list[GroundTruthRecord],
    predictions: dict[str, Prediction],
    strategy: ValidationStrategy | None = None,
) -> ValidationReport:
    strat = strategy or default_strategy(ground_truth)

    overall = ConfusionMatrix()
    by_rule: dict[str, ConfusionMatrix] = {}
    by_camera: dict[str, ConfusionMatrix] = {}
    comparisons: list[Comparison] = []
    missing: list[Comparison] = []

    for gt in ground_truth:
        pred = predictions.get(gt.video_id)
        expected = strat.expected_label(gt)

        if pred is None:
            cmp = Comparison(
                video_id=gt.video_id,
                video_url=gt.video_url,
                expected=expected,
                predicted=None,
                outcome="MISSING",
                rule_id=gt.rule_id,
                camera_id=gt.camera_id,
            )
            comparisons.append(cmp)
            missing.append(cmp)
            continue

        # 1 video -> list outcome (thường 1 phần tử; luật chấm theo event trả nhiều hơn).
        outcomes = strat.classify(gt, pred)
        predicted = strat.predicted_label(gt, pred)
        detail = strat.detail(gt, pred)
        n = len(outcomes)

        for i, outcome in enumerate(outcomes):
            overall.bump(outcome)
            by_rule.setdefault(gt.rule_id or "_", ConfusionMatrix()).bump(outcome)
            by_camera.setdefault(gt.camera_id or "_", ConfusionMatrix()).bump(outcome)

            comparisons.append(
                Comparison(
                    video_id=gt.video_id,
                    video_url=gt.video_url,
                    expected=expected,
                    predicted=predicted,
                    outcome=outcome,
                    rule_id=gt.rule_id,
                    camera_id=gt.camera_id,
                    confidence=pred.confidence,
                    detail=detail,
                    event_index=i,
                    event_count=n,
                )
            )

    return ValidationReport(
        overall=overall,
        by_rule=by_rule,
        by_camera=by_camera,
        comparisons=comparisons,
        missing=missing,
        strategy=strat.name,
    )
