"""So khớp prediction với ground truth -> kết quả + metrics.

Hỗ trợ tổng hợp (overall) và bóc tách theo rule / camera để dễ soi lỗi.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import ConfusionMatrix
from .models import GroundTruthRecord, Prediction


@dataclass
class Comparison:
    video_id: str
    video_url: str
    expected: bool
    predicted: bool | None       # None = không có kết quả phân tích
    outcome: str                 # TP/FP/FN/TN/MISSING
    rule_id: str = ""
    camera_id: str = ""
    confidence: float | None = None


@dataclass
class ValidationReport:
    overall: ConfusionMatrix
    by_rule: dict[str, ConfusionMatrix]
    by_camera: dict[str, ConfusionMatrix]
    comparisons: list[Comparison]
    missing: list[Comparison] = field(default_factory=list)


def validate(
    ground_truth: list[GroundTruthRecord],
    predictions: dict[str, Prediction],
) -> ValidationReport:
    overall = ConfusionMatrix()
    by_rule: dict[str, ConfusionMatrix] = {}
    by_camera: dict[str, ConfusionMatrix] = {}
    comparisons: list[Comparison] = []
    missing: list[Comparison] = []

    for gt in ground_truth:
        pred = predictions.get(gt.video_id)

        if pred is None:
            cmp = Comparison(
                video_id=gt.video_id,
                video_url=gt.video_url,
                expected=gt.expected_violation,
                predicted=None,
                outcome="MISSING",
                rule_id=gt.rule_id,
                camera_id=gt.camera_id,
            )
            comparisons.append(cmp)
            missing.append(cmp)
            continue

        outcome = overall.add(gt.expected_violation, pred.has_violation)
        by_rule.setdefault(gt.rule_id or "_", ConfusionMatrix()).add(
            gt.expected_violation, pred.has_violation
        )
        by_camera.setdefault(gt.camera_id or "_", ConfusionMatrix()).add(
            gt.expected_violation, pred.has_violation
        )
        comparisons.append(
            Comparison(
                video_id=gt.video_id,
                video_url=gt.video_url,
                expected=gt.expected_violation,
                predicted=pred.has_violation,
                outcome=outcome,
                rule_id=gt.rule_id,
                camera_id=gt.camera_id,
                confidence=pred.confidence,
            )
        )

    return ValidationReport(
        overall=overall,
        by_rule=by_rule,
        by_camera=by_camera,
        comparisons=comparisons,
        missing=missing,
    )
