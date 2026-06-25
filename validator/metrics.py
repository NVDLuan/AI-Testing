"""Tính confusion matrix và các chỉ số ở mức video (boolean).

Quy ước nhãn dương (positive) = "có vi phạm".
    TP: GT có vi phạm  & dự đoán có vi phạm
    FP: GT không       & dự đoán có      -> báo động giả (false alarm)
    FN: GT có          & dự đoán không   -> bỏ sót
    TN: GT không       & dự đoán không

    Recall (sensitivity) = TP / (TP + FN)
    FAR (False Alarm Rate) = FP / (TP + FP)
"""
from __future__ import annotations

from dataclasses import dataclass


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


@dataclass
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, expected: bool, predicted: bool) -> str:
        """Cập nhật ma trận, trả về outcome ('TP'|'FP'|'FN'|'TN')."""
        if expected and predicted:
            self.tp += 1
            return "TP"
        if not expected and predicted:
            self.fp += 1
            return "FP"
        if expected and not predicted:
            self.fn += 1
            return "FN"
        self.tn += 1
        return "TN"

    def bump(self, outcome: str) -> None:
        """Cộng trực tiếp theo outcome đã tính sẵn (dùng cho ma trận gộp 3-state)."""
        if outcome == "TP":
            self.tp += 1
        elif outcome == "FP":
            self.fp += 1
        elif outcome == "FN":
            self.fn += 1
        elif outcome == "TN":
            self.tn += 1

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def recall(self) -> float:
        return _safe_div(self.tp, self.tp + self.fn)

    @property
    def far(self) -> float:
        return _safe_div(self.fp, self.tp + self.fp)

    @property
    def precision(self) -> float:
        return _safe_div(self.tp, self.tp + self.fp)

    @property
    def accuracy(self) -> float:
        return _safe_div(self.tp + self.tn, self.total)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return _safe_div(2 * p * r, p + r)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "total": self.total,
            "recall": round(self.recall, 4),
            "far": round(self.far, 4),
            "precision": round(self.precision, 4),
            "accuracy": round(self.accuracy, 4),
            "f1": round(self.f1, 4),
        }
