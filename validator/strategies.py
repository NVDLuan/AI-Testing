"""Luật so khớp (validation strategy) — phần CẮM ĐƯỢC của validator.

`validate()` lo phần khung: duyệt từng video, gom ConfusionMatrix (overall/rule/camera),
thu thập Comparison. Còn *quyết định một video đúng/sai thế nào* nằm ở đây, mỗi luật
một class -> thêm cách chấm mới = thêm 1 strategy, KHÔNG sửa lõi validator.

Hợp đồng:
  - `classify(gt, pred)` trả `list[str]` — MỖI PHẦN TỬ là 'TP'|'FP'|'FN'|'TN' của một
    "đơn vị chấm". Đa số luật chỉ có 1 đơn vị/video (list 1 phần tử, vd binary/3-state
    gộp); luật nào chấm được TỪNG EVENT riêng (vd hand-washing, 1 video có thể nhiều
    event) thì trả 1 phần tử/event — `validate()` sẽ bump ConfusionMatrix và tạo
    Comparison cho từng phần tử, nên FAR/Recall khi đó tính trên đơn vị EVENT.
  - `expected_label` / `predicted_label`: giá trị hiển thị trong report (bool hoặc chuỗi).
  - `detail(gt, pred)`: thông tin phụ tuỳ luật (vd số object khớp, zone, IoU) gắn vào
    Comparison cho báo cáo — mặc định None.

Mở rộng sau này (verify detection): viết một subclass đọc thêm `pred.events` (bbox,
zone, count...) và `gt.raw` để tự quyết định TP/FP/FN/TN, đính kèm số liệu qua `detail()`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import GroundTruthRecord, Prediction

# Tên trạng thái 3-state (bv_c) để hiển thị.
STATE_NO_TRIGGER = "No Trigger"
STATE_TRIGGER_NO_VIOLATION = "Trigger (No Violation)"
STATE_TRIGGER_VIOLATION = "Trigger (Violation)"


def _confusion(expected: bool, predicted: bool) -> str:
    """Phân loại boolean chuẩn (positive = expected/predicted True)."""
    if expected and predicted:
        return "TP"
    if not expected and predicted:
        return "FP"
    if expected and not predicted:
        return "FN"
    return "TN"


def _state(triggered: bool, violation: bool) -> str:
    if not triggered:
        return STATE_NO_TRIGGER
    return STATE_TRIGGER_VIOLATION if violation else STATE_TRIGGER_NO_VIOLATION


class ValidationStrategy(ABC):
    """Luật so khớp 1 video. Stateless — dùng lại được cho mọi rule/camera."""

    name: str = "base"

    @abstractmethod
    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        """Trả list outcome 'TP'|'FP'|'FN'|'TN', 1 phần tử/đơn vị chấm (thường 1/video)."""
        raise NotImplementedError

    @abstractmethod
    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        raise NotImplementedError

    @abstractmethod
    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        raise NotImplementedError

    def detail(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any] | None:
        """Thông tin phụ cho Comparison (mặc định không có)."""
        return None


class BinaryViolationStrategy(ValidationStrategy):
    """Chấm đúng/sai theo nhãn vi phạm boolean (linfox, EWS...).

    positive = "có vi phạm". expected = gt.expected_violation; predicted = pred.has_violation.
    """

    name = "binary_violation"

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return [_confusion(gt.expected_violation, pred.has_violation)]

    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        return gt.expected_violation

    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        return pred.has_violation


class TriggerViolationStrategy(ValidationStrategy):
    """Chấm 3-trạng-thái gộp 1 ma trận (bv_c).

        TP = trigger ĐÚNG (cần trigger & có trigger) và violation khớp expected
        FP = có trigger nhưng SAI (violation sai, hoặc lẽ ra không nên trigger)
        FN = cần trigger nhưng không trigger (bỏ sót trigger)
        TN = không cần trigger & không trigger
    Lưu ý: "trigger đúng nhưng bỏ sót vi phạm" = FP (không phải FN).
    """

    name = "trigger_violation"

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        exp_trig = bool(gt.expected_triggered)
        pred_trig = bool(pred.triggered)
        if not pred_trig:
            return ["FN"] if exp_trig else ["TN"]
        if exp_trig and gt.expected_violation == bool(pred.has_violation):
            return ["TP"]
        return ["FP"]

    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        return _state(bool(gt.expected_triggered), gt.expected_violation)

    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        return _state(bool(pred.triggered), bool(pred.has_violation))


def default_strategy(ground_truth: list[GroundTruthRecord]) -> ValidationStrategy:
    """Tự chọn luật theo dữ liệu: có expected_triggered -> 3-state; còn lại -> binary."""
    if any(gt.expected_triggered is not None for gt in ground_truth):
        return TriggerViolationStrategy()
    return BinaryViolationStrategy()
