"""Luật chấm bv_c (rule "Hygiene") — mỗi tiêu chí là 1 `ValidationStrategy` độc lập.

Trước đây một class (`HandWashingStrategy` + `V2..V6`) gộp cả 3 tiêu chí (trigger,
Step Sequence, count từng bước) qua đa kế thừa — thêm tiêu chí mới phải tổ hợp thêm
class. Giờ mỗi tiêu chí là 1 strategy riêng, có `name` riêng; `tenants/factory.py` xếp
chúng thành CHAIN theo `--hw-layers`. Mỗi strategy trong chain chạy `validate()` độc
lập, sinh 1 `ValidationReport` riêng — report coi mỗi report này là 1 "rule ảo"
(`"<rule_code> - <name>"`). Thêm tiêu chí mới = thêm 1 class ở đây + đăng ký trong
`BvCFactory`, không đụng class khác.

Phạm vi chấm:
  - `TriggerStrategy` áp dụng MỌI video.
  - `ViolationStrategy`/`SequenceStrategy`/`CountStrategy` CHỈ áp dụng khi GT cần
    trigger VÀ model có trigger (lỗi trigger đã tính đủ ở tầng Trigger) — video ngoài
    phạm vi này (hoặc sheet không khai dữ liệu cho tiêu chí đó) `classify()` trả `[]`;
    `validate()` bỏ qua hoàn toàn (không Comparison, không tính vào ma trận).

Nguồn dữ liệu: expected từ `gt.raw` (cột `B0..B6`, `Step Sequence`), actual từ
`pred.events` (chuẩn hoá ở `handwashing_client._parse_event` từ
`detection_rule_infos[].params`). `ViolationStrategy` chấm 1 lần/video (`pred.has_violation`
gộp sẵn mọi event); `SequenceStrategy`/`CountStrategy` chấm theo TỪNG event (1 video có
thể nhiều lần rửa tay) — nếu không event nào đạt thì cộng thêm 1 "FN" (bỏ sót), khớp
hành vi cũ.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .models import GroundTruthRecord, Prediction
from .strategies import ValidationStrategy

COUNT_THRESHOLD = 5
_B_COL = re.compile(r"^B\d+$", re.IGNORECASE)


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def _confusion(expected: bool, predicted: bool) -> str:
    if expected and predicted:
        return "TP"
    if not expected and predicted:
        return "FP"
    if expected and not predicted:
        return "FN"
    return "TN"


def _is_subsequence(needle: list[int], haystack: list[int]) -> bool:
    it = iter(haystack)
    return all(x in it for x in needle)


def _lcs_ratio(expected: list[int], actual: list[int]) -> float:
    """LCS(expected, actual) / len(expected) -> độ tương đồng [0,1]."""
    m, n = len(expected), len(actual)
    if m == 0:
        return 1.0
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            curr[j] = (prev[j - 1] + 1) if expected[i - 1] == actual[j - 1] else max(prev[j], curr[j - 1])
        prev = curr
    return prev[n] / m


def _seq_str(seq: list[int]) -> str:
    return ",".join(str(x) for x in (seq or []))


def _counts_kv(counts: dict) -> str:
    return ",".join(f"{k}:{counts[k]}" for k in sorted(counts))


class _HandWashingBase(ValidationStrategy):
    """Helper dùng chung: đọc `gt.raw` (B0..B6, Step Sequence) + chấm theo từng event.

    Không tự implement `classify`/`expected_label`/`predicted_label` (vẫn abstract) —
    các strategy con dùng lại các helper này, không qua đa kế thừa.
    """

    @staticmethod
    def _in_scope(gt: GroundTruthRecord, pred: Prediction) -> bool:
        """Chỉ Violation/Sequence/Count mới cần gate này: GT cần trigger & model có trigger."""
        return bool(gt.expected_triggered) and bool(pred.triggered)

    @staticmethod
    def _expected_counts(gt: GroundTruthRecord) -> dict[str, int]:
        return {
            str(k).strip().upper(): _to_int(v)
            for k, v in (gt.raw or {}).items()
            if _B_COL.match(str(k).strip())
        }

    @staticmethod
    def _expected_sequence(gt: GroundTruthRecord) -> list[int]:
        raw = (gt.raw or {}).get("Step Sequence", "")
        return [int(x) for x in re.findall(r"\d+", str(raw))]

    @staticmethod
    def _check_counts(
            expected: dict[str, int], actual: dict[str, Any], threshold: int = COUNT_THRESHOLD,
    ) -> tuple[list[dict], float | None]:
        results: list[dict] = []
        matched = 0
        for code, exp in sorted(expected.items()):
            act = _to_int(actual.get(code, 0))
            ok = (exp >= threshold) == (act >= threshold)
            matched += ok
            results.append({"step": code, "expected": exp, "actual": act, "passed": ok})
        acc = round(matched / len(results) * 100, 1) if results else None
        return results, acc

    @staticmethod
    def _check_sequence(expected: list[int], actual: list[int]) -> dict:
        if not expected:
            return {"expected": expected, "actual": actual, "passed": True, "similarity": 1.0}
        passed = bool(actual) and _is_subsequence(expected, actual)
        return {
            "expected": expected, "actual": actual, "passed": passed,
            "similarity": round(_lcs_ratio(expected, actual), 2),
        }

    @staticmethod
    def _event_scores(pred: Prediction, ok_fn: Callable[[dict], bool]) -> list[str]:
        """TP/FP theo từng event; không event nào đạt -> cộng thêm 1 FN (bỏ sót)."""
        if not pred.events:
            return ["FP"]  # có trigger nhưng không có event nào để chấm -> coi là sai
        outcomes = ["TP" if ok_fn(ev) else "FP" for ev in pred.events]
        return outcomes


class TriggerStrategy(ValidationStrategy):
    """Tầng Trigger: chỉ chấm hệ thống CÓ trigger đúng lúc không (bỏ qua violation).

    Áp dụng MỌI video. TP/FN/FP/TN chuẩn theo positive = có trigger.
    """

    name = "Trigger"

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return [_confusion(bool(gt.expected_triggered), bool(pred.triggered))]

    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        return bool(gt.expected_triggered)

    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        return bool(pred.triggered)


class ViolationStrategy(_HandWashingBase):
    """Tầng Violation: `pred.has_violation` so với `gt.expected_violation` — thuần
    boolean, positive = "có vi phạm" (giống `BinaryViolationStrategy` linfox, không có
    điều kiện phụ nào về count).

    Chỉ chấm video mà GT cần trigger VÀ model có trigger — video khác `classify()`
    trả `[]` (lỗi trigger đã tính đủ ở `TriggerStrategy`).
    """

    name = "Violation"

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        if not self._in_scope(gt, pred):
            return []
        return [_confusion(bool(gt.expected_violation), bool(pred.has_violation))]

    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        return gt.expected_violation

    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        return bool(pred.has_violation)


class SequenceStrategy(_HandWashingBase):
    """Tầng Sequence: chuỗi bước (`Step Sequence` trong sheet) so với `event.sequence`.

    `exact=False` (mặc định): expected phải là SUBSEQUENCE của event (lenient).
    `exact=True`: tập bước phải khớp CHÍNH XÁC.
    Chỉ chấm khi trigger đúng phạm vi VÀ sheet có khai Step Sequence.
    """

    name = "Sequence"

    def __init__(self, exact: bool = False) -> None:
        self._exact = exact

    def _event_ok(self, expected: list[int], actual: list[int]) -> bool:
        if self._exact:
            return set(expected) == set(actual)
        return bool(actual) and _is_subsequence(expected, actual)

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        expected_seq = self._expected_sequence(gt)
        if not expected_seq or not self._in_scope(gt, pred):
            return []
        return self._event_scores(pred, lambda ev: self._event_ok(expected_seq, ev.get("sequence", []) or []))

    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        return _seq_str(self._expected_sequence(gt))

    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        seqs = [ev.get("sequence", []) for ev in (pred.events or []) if ev.get("sequence")]
        return " | ".join(_seq_str(s) for s in seqs)

    def detail(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any] | None:
        expected_seq = self._expected_sequence(gt)
        if not expected_seq or not self._in_scope(gt, pred):
            return None
        return {
            "exact": self._exact,
            "events": [
                {
                    "event_code": ev.get("event_code", ""),
                    "sequence": self._check_sequence(expected_seq, ev.get("sequence", []) or []),
                }
                for ev in pred.events
            ],
        }


class CountStrategy(_HandWashingBase):
    """Tầng Count: số lần từng bước (cột `B0..B6`) so với `event.counts`, theo NGƯỠNG.

    Khớp = `(expected>=threshold) == (actual>=threshold)` cho MỌI bước expected.
    Chỉ chấm khi trigger đúng phạm vi VÀ sheet có khai ít nhất 1 cột B.
    """

    name = "Count"

    def __init__(self, threshold: int = COUNT_THRESHOLD) -> None:
        self._threshold = threshold

    def _event_ok(self, expected: dict[str, int], actual: dict[str, Any]) -> bool:
        steps, _acc = self._check_counts(expected, actual, self._threshold)
        return bool(steps) and all(s["passed"] for s in steps)

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        expected = self._expected_counts(gt)
        if not expected or not self._in_scope(gt, pred):
            return []
        return self._event_scores(pred, lambda ev: self._event_ok(expected, ev.get("counts", {})))

    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        return _counts_kv(self._expected_counts(gt))

    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        merged: dict[str, int] = {}
        for ev in (pred.events or []):
            for code, c in (ev.get("counts") or {}).items():
                cu = str(code).strip().upper()
                merged[cu] = max(merged.get(cu, 0), _to_int(c))
        return _counts_kv(merged)

    def detail(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any] | None:
        expected = self._expected_counts(gt)
        if not expected or not self._in_scope(gt, pred):
            return None
        events = []
        for ev in pred.events:
            steps, acc = self._check_counts(expected, ev.get("counts", {}), self._threshold)
            events.append({"event_code": ev.get("event_code", ""), "steps": steps, "step_accuracy": acc})
        return {"events": events}
