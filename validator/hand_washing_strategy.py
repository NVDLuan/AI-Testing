"""HandWashingStrategy — luật chấm cho tenant bv_c (rule "Hygiene").

OUTCOME (TP/FP/FN/TN) kế thừa nguyên `TriggerViolationStrategy` — gộp 1 ma trận theo
trigger + violation (xem strategies.py). Phần MỚI ở đây là `detail()`: phân tích sâu
từng bước rửa tay để đổ vào `Comparison.detail` (report JSON/HTML đọc được) — đây là
chỗ "output linh hoạt": muốn thêm tiêu chí (thời gian, thứ tự, out-of-order...) chỉ cần
bồi vào dict detail, không đụng tới cách tính FAR/Recall.

So khớp:
  - violation : `pred event.violation` vs `gt.expected_violation` (đã quyết outcome).
  - step count: mỗi bước B0..B6 — expected (cột B<n> trong sheet) vs actual (count từ
    event), so theo NGƯỠNG TỰ DO 5 (cùng phía ngưỡng = đạt).
  - sequence  : expected (cột "Step Sequence") phải là subsequence của actual; báo kèm
    similarity (LCS) khi lệch.

Nguồn dữ liệu:
  - expected: `gt.raw` (dòng sheet) — các cột `B0..B6` (số lần) + `Step Sequence`.
  - actual  : `pred.events` (đã chuẩn hoá ở handwashing_client._parse_event):
    [{violation, counts{code:int}, sequence[int], duration, ...}, ...].
"""
from __future__ import annotations

import re
from typing import Any

from .models import GroundTruthRecord, Prediction
from .strategies import TriggerViolationStrategy

_COUNT_THRESHOLD = 5
_B_COL = re.compile(r"^B\d+$", re.IGNORECASE)


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


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


class HandWashingStrategy(TriggerViolationStrategy):
    name = "hand_washing"

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return self._classify_events(gt, pred, check_status=True)

    # --- expected từ sheet -----------------------------------------------------

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

    # --- checks ----------------------------------------------------------------

    @staticmethod
    def _check_counts(expected: dict[str, int], actual: dict[str, Any]) -> tuple[list[dict], float | None]:
        results: list[dict] = []
        matched = 0
        for code, exp in sorted(expected.items()):
            act = _to_int(actual.get(code, 0))
            ok = (exp >= _COUNT_THRESHOLD) == (act >= _COUNT_THRESHOLD)
            matched += ok
            results.append({"step": code, "expected": exp, "actual": act, "passed": ok})
        acc = round(matched / len(results) * 100, 1) if results else None
        return results, acc

    @classmethod
    def _check_sequence(cls, expected: list[int], actual: list[int]) -> dict:
        if not expected:
            return {"expected": expected, "actual": actual, "passed": True, "similarity": 1.0}
        passed = bool(actual) and _is_subsequence(expected, actual)
        return {
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "similarity": round(_lcs_ratio(expected, actual), 2),
        }

    @staticmethod
    def _event_violation_ok(gt: GroundTruthRecord, ev: dict) -> bool:
        return gt.expected_violation == bool(ev.get("violation"))

    def _event_sequence_ok(self, gt: GroundTruthRecord, ev: dict, *, exact: bool = False) -> bool:
        expected_seq = self._expected_sequence(gt)
        actual_seq = ev.get("sequence", []) or []
        if exact:
            return set(expected_seq) == set(actual_seq)
        if not expected_seq:
            return True
        return bool(actual_seq) and _is_subsequence(expected_seq, actual_seq)

    def _event_counts_ok(self, gt: GroundTruthRecord, ev: dict) -> bool:
        expected = self._expected_counts(gt)
        if not expected:
            return True
        steps, _acc = self._check_counts(expected, ev.get("counts", {}))
        return bool(steps) and all(s["passed"] for s in steps)

    def _event_outcome(
            self, gt: GroundTruthRecord, ev: dict,
            *, check_status: bool, check_sequence: bool, check_counts: bool, exact_sequence: bool,
    ) -> str:
        ok = True
        if check_status:
            ok = ok and self._event_violation_ok(gt, ev)
        if check_sequence:
            ok = ok and self._event_sequence_ok(gt, ev, exact=exact_sequence)
        if check_counts:
            ok = ok and self._event_counts_ok(gt, ev)
        return "TP" if ok else "FP"

    def _classify_events(
            self, gt: GroundTruthRecord, pred: Prediction,
            *, check_status: bool, check_sequence: bool = False, check_counts: bool = False,
            exact_sequence: bool = False,
    ) -> list[str]:
        exp_trig = bool(gt.expected_triggered)
        if not bool(pred.triggered):
            return ["FN"] if exp_trig else ["TN"]
        if not pred.events:
            return ["FP"]  # có trigger nhưng không có event nào để chấm -> coi là sai
        outcomes = [
            self._event_outcome(
                gt, ev,
                check_status=check_status, check_sequence=check_sequence,
                check_counts=check_counts, exact_sequence=exact_sequence,
            )
            for ev in pred.events
        ]
        if "TP" not in outcomes:
            outcomes.append("FN")  # không event nào đạt -> cộng thêm 1 FN (bỏ sót)
        return outcomes

    # --- detail (output linh hoạt) ---------------------------------------------

    def detail(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any] | None:
        info: dict[str, Any] = {
            "expected_state": self.expected_label(gt),
            "predicted_state": self.predicted_label(gt, pred),
            "expected_violation": gt.expected_violation,
            "predicted_violation": bool(pred.has_violation),
        }
        if not pred.events:
            return info  # No Trigger / không có event -> chỉ trạng thái

        expected_counts = self._expected_counts(gt)
        expected_seq = self._expected_sequence(gt)

        events: list[dict] = []
        for ev in pred.events:
            steps, acc = self._check_counts(expected_counts, ev.get("counts", {}))
            events.append({
                "event_code": ev.get("event_code", ""),
                "violation": ev.get("violation"),
                "outcome": self._event_outcome(
                    gt, ev,
                    check_status=True, check_sequence=bool(expected_seq),
                    check_counts=bool(expected_counts), exact_sequence=False,
                ),
                "step_accuracy": acc,
                "steps": steps,
                "sequence": self._check_sequence(expected_seq, ev.get("sequence", [])),
                "duration": ev.get("duration"),
            })
        info["events"] = events
        # tóm tắt nhiều event cho dễ đọc nhanh trong report (1 video có thể nhiều event)
        accs = [e["step_accuracy"] for e in events if e["step_accuracy"] is not None]
        info["num_events"] = len(events)
        info["any_violation"] = any(bool(e["violation"]) for e in events)
        info["step_accuracy"] = round(sum(accs) / len(accs), 1) if accs else None
        info["sequence_ok"] = all(e["sequence"]["passed"] for e in events)
        return info


class HandWashingStrategyV2(HandWashingStrategy):
    """V2: outcome dựa trên `expected_status` + `Step Sequence`.

    Khác V1 (chỉ trigger + violation): V2 còn YÊU CẦU chuỗi bước khớp thì mới TP.
        - không trigger        -> FN (cần trigger) / TN (không cần) — như V1.
        - có trigger:
            TP  = cần trigger & violation khớp & Step Sequence khớp.
            FP  = có trigger nhưng SAI bất kỳ: violation sai, lẽ ra không trigger,
                  HOẶC sequence không khớp (model bắt sai/thiếu thứ tự bước).
    Sequence khớp = chuỗi `Step Sequence` (cột sheet) là subsequence của sequence
    trong ÍT NHẤT một event (lenient: chỉ cần model bắt đúng ở một lần). Sheet không
    có Step Sequence -> coi như không ràng buộc sequence (chỉ chấm theo status).
    """

    name = "hand_washing_v2"

    def _sequence_ok(self, gt: GroundTruthRecord, pred: Prediction) -> bool:
        expected_seq = self._expected_sequence(gt)
        if not expected_seq:
            return True  # không khai báo sequence -> bỏ qua ràng buộc này
        return any(
            _is_subsequence(expected_seq, ev.get("sequence", []) or [])
            for ev in (pred.events or [])
        )

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return self._classify_events(gt, pred, check_status=True, check_sequence=True)

    def detail(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any] | None:
        info = super().detail(gt, pred) or {}
        # Phơi rõ 2 thành phần quyết định outcome của V2 để report biết vì sao TP/FP.
        info["sequence_enforced"] = True
        info["sequence_match"] = self._sequence_ok(gt, pred)
        info["status_match"] = (
                bool(gt.expected_triggered) == bool(pred.triggered)
                and gt.expected_violation == bool(pred.has_violation)
        )
        return info


class HandWashingStrategyV3(HandWashingStrategy):
    """V3: giống V2 nhưng chấm theo COUNT từng bước thay vì Step Sequence.

        - không trigger -> FN (cần trigger) / TN (không cần).
        - có trigger:
            TP = cần trigger & violation khớp & count từng bước khớp.
            FP = có trigger nhưng SAI: violation sai, lẽ ra không trigger, HOẶC count
                 của bước nào đó không khớp.
    Count khớp = mọi bước expected (cột `B0..B6` trong sheet) cùng phía NGƯỠNG 5 với
    count thực tế (`(exp>=5) == (act>=5)`), xét trên ÍT NHẤT một event. Sheet không khai
    báo cột B -> không ràng buộc count (chỉ chấm theo status).
    """

    name = "hand_washing_v3"

    def _counts_ok(self, gt: GroundTruthRecord, pred: Prediction) -> bool:
        expected = self._expected_counts(gt)
        if not expected:
            return True  # không khai báo count -> bỏ qua ràng buộc này
        for ev in (pred.events or []):
            steps, _acc = self._check_counts(expected, ev.get("counts", {}))
            if steps and all(s["passed"] for s in steps):
                return True
        return False

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return self._classify_events(gt, pred, check_status=True, check_counts=True)

    def detail(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any] | None:
        info = super().detail(gt, pred) or {}
        info["counts_enforced"] = True
        info["counts_match"] = self._counts_ok(gt, pred)
        info["status_match"] = (
                bool(gt.expected_triggered) == bool(pred.triggered)
                and gt.expected_violation == bool(pred.has_violation)
        )
        return info


class HandWashingStrategyV4(HandWashingStrategyV2, HandWashingStrategyV3):
    """V4: chấm CẢ 3 — status (trigger+violation) + Step Sequence + count từng bước.

        - không trigger -> FN (cần trigger) / TN (không cần).
        - có trigger:
            TP = cần trigger & violation khớp & Step Sequence khớp & count khớp.
            FP = sai bất kỳ một trong các điều kiện trên.
    Dùng lại `_sequence_ok` (V2) và `_counts_ok` (V3) qua đa kế thừa; `detail()` kế thừa
    chuỗi nên có đủ cả `sequence_match` lẫn `counts_match`.
    """

    name = "hand_washing_v4"

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return self._classify_events(gt, pred, check_status=True, check_sequence=True, check_counts=True)


class HandWashingStrategyV5(HandWashingStrategyV4):
    """V5: như V4 (status + Step Sequence + count) nhưng KHÔNG chấm `expected_violation`.

        - không trigger -> FN (cần trigger) / TN (không cần).
        - có trigger:
            TP = cần trigger & Step Sequence khớp & count khớp (BỎ QUA violation).
            FP = sai bất kỳ: lẽ ra không trigger, sequence sai, HOẶC count sai.
    Dùng khi chỉ quan tâm model bắt đúng QUY TRÌNH (thứ tự + số lần từng bước), không
    quan tâm kết luận vi phạm có khớp ground truth hay không. `detail()` kế thừa V4 nên
    vẫn phơi `sequence_match`/`counts_match`/`status_match`.
    """

    name = "hand_washing_v5"

    @staticmethod
    def _check_counts(expected: dict[str, int], actual: dict[str, Any]) -> tuple[list[dict], float | None]:
        results: list[dict] = []
        matched = 0
        for code, exp in sorted(expected.items()):
            act = _to_int(actual.get(code, 0))
            ok = abs(exp - act) <= 1
            matched += ok
            results.append({"step": code, "expected": exp, "actual": act, "passed": ok})
        acc = round(matched / len(results) * 100, 1) if results else None
        return results, acc

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return self._classify_events(gt, pred, check_status=False, check_sequence=True, check_counts=True)


class HandWashingStrategyV6(HandWashingStrategyV4):
    """V4: chấm CẢ 3 — status (trigger+violation) + Step Sequence + count từng bước.

        - không trigger -> FN (cần trigger) / TN (không cần).
        - có trigger:
            TP = cần trigger & violation khớp & Step Sequence khớp & count khớp.
            FP = sai bất kỳ một trong các điều kiện trên.
    Khác V4: sequence phải khớp CHÍNH XÁC (tập hợp bước bằng nhau), không chỉ là
    subsequence — dùng `exact_sequence=True` khi chấm từng event.
    """

    name = "hand_washing_v6"

    def _sequence_ok(self, gt: GroundTruthRecord, pred: Prediction) -> bool:
        expected_seq = set(self._expected_sequence(gt))
        return any(
            expected_seq == set(ev.get("sequence", []) or [])
            for ev in (pred.events or [])
        )

    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return self._classify_events(
            gt, pred, check_status=True, check_sequence=True, check_counts=True, exact_sequence=True,
        )
