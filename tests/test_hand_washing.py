"""Test 4 strategy chấm bv_c (hand-washing). Chạy: python -m tests.test_hand_washing"""
from validator.hand_washing_strategy import (
    CountStrategy, SequenceStrategy, TriggerStrategy, ViolationStrategy,
)
from validator.models import GroundTruthRecord, Prediction


def _gt(expected_triggered, expected_violation=False, raw=None) -> GroundTruthRecord:
    return GroundTruthRecord(
        "v1", "u1", expected_violation, "Hygiene", "cam1", "v1",
        expected_triggered=expected_triggered, raw=raw or {},
    )


def _pred(triggered, has_violation=False, events=None) -> Prediction:
    return Prediction("v1", has_violation, triggered=triggered, events=events or [])


def test_trigger_strategy():
    strat = TriggerStrategy()
    assert strat.classify(_gt(True), _pred(True)) == ["TP"]
    assert strat.classify(_gt(True), _pred(False)) == ["FN"]
    assert strat.classify(_gt(False), _pred(True)) == ["FP"]
    assert strat.classify(_gt(False), _pred(False)) == ["TN"]


def test_violation_strategy_out_of_scope():
    strat = ViolationStrategy()
    # GT không cần trigger -> ngoài phạm vi, bất kể model làm gì.
    assert strat.classify(_gt(False), _pred(True)) == []
    # GT cần trigger nhưng model không trigger -> lỗi đã tính ở Trigger, layer này bỏ qua.
    assert strat.classify(_gt(True), _pred(False)) == []


def test_violation_strategy_in_scope_no_cond():
    # Không khai cột B -> cond luôn đạt, chỉ chấm theo violation flag.
    strat = ViolationStrategy()
    gt = _gt(True, expected_violation=True)
    ok_event = [{"event_code": "e1", "violation": True}]
    bad_event = [{"event_code": "e1", "violation": False}]
    assert strat.classify(gt, _pred(True, events=ok_event)) == ["TP"]
    # violation flag SAI (GT cần vi phạm nhưng event không vi phạm) -> FN (không phải FP).
    assert strat.classify(gt, _pred(True, events=bad_event)) == ["FN", "FN"]
    # có trigger nhưng không event nào để chấm -> FP.
    assert strat.classify(gt, _pred(True, events=[])) == ["FP"]


def test_violation_strategy_cond_violation():
    # GT: Violation -> cond đạt khi count lệch expected <= 1.
    strat = ViolationStrategy()
    gt = _gt(True, expected_violation=True, raw={"B1": 2})
    flag_ok_cond_ok = [{"violation": True, "counts": {"B1": 3}}]    # lệch 1 -> đạt
    flag_ok_cond_bad = [{"violation": True, "counts": {"B1": 5}}]   # lệch 3 -> không đạt
    assert strat.classify(gt, _pred(True, events=flag_ok_cond_ok)) == ["TP"]
    # violation flag ĐÚNG nhưng cond SAI -> FP (không tin kết luận vi phạm này).
    assert strat.classify(gt, _pred(True, events=flag_ok_cond_bad)) == ["FP", "FN"]


def test_violation_strategy_cond_no_violation():
    # GT: No Violation -> cond đạt khi MỌI count >= 5.
    strat = ViolationStrategy()
    gt = _gt(True, expected_violation=False, raw={"B1": 6, "B2": 7})
    flag_ok_cond_ok = [{"violation": False, "counts": {"B1": 6, "B2": 8}}]
    flag_ok_cond_bad = [{"violation": False, "counts": {"B1": 2, "B2": 8}}]  # B1 < 5
    assert strat.classify(gt, _pred(True, events=flag_ok_cond_ok)) == ["TN"]
    # violation flag ĐÚNG (đều false) nhưng cond SAI -> FP.
    assert strat.classify(gt, _pred(True, events=flag_ok_cond_bad)) == ["FP"]


def test_sequence_strategy_skips_without_step_sequence():
    strat = SequenceStrategy()
    gt = _gt(True, raw={})
    assert strat.classify(gt, _pred(True, events=[{"sequence": [1, 2, 3]}])) == []


def test_sequence_strategy_subsequence():
    strat = SequenceStrategy(exact=False)
    gt = _gt(True, raw={"Step Sequence": "1,2,3"})
    good = [{"sequence": [1, 0, 2, 0, 3]}]  # subsequence khớp (lenient)
    bad = [{"sequence": [3, 2, 1]}]         # sai thứ tự
    assert strat.classify(gt, _pred(True, events=good)) == ["TP"]
    assert strat.classify(gt, _pred(True, events=bad)) == ["FP", "FN"]


def test_sequence_strategy_exact():
    strat = SequenceStrategy(exact=True)
    gt = _gt(True, raw={"Step Sequence": "1,2,3"})
    subset_only = [{"sequence": [1, 2]}]  # subsequence nhưng thiếu bước 3 -> exact fail
    exact_set = [{"sequence": [3, 1, 2]}]  # đúng tập hợp (thứ tự khác cũng được)
    assert strat.classify(gt, _pred(True, events=subset_only)) == ["FP", "FN"]
    assert strat.classify(gt, _pred(True, events=exact_set)) == ["TP"]


def test_count_strategy():
    strat = CountStrategy(threshold=5)
    gt = _gt(True, raw={"B1": 5, "B2": 3})
    ok_event = [{"counts": {"B1": 7, "B2": 1}}]   # cùng phía ngưỡng 5 cho cả 2 bước
    bad_event = [{"counts": {"B1": 2, "B2": 1}}]  # B1 lệch phía ngưỡng
    assert strat.classify(gt, _pred(True, events=ok_event)) == ["TP"]
    assert strat.classify(gt, _pred(True, events=bad_event)) == ["FP", "FN"]


def test_count_strategy_skips_without_b_columns():
    strat = CountStrategy()
    gt = _gt(True, raw={})
    assert strat.classify(gt, _pred(True, events=[{"counts": {"B1": 5}}])) == []


if __name__ == "__main__":
    test_trigger_strategy()
    test_violation_strategy_out_of_scope()
    test_violation_strategy_in_scope_no_cond()
    test_violation_strategy_cond_violation()
    test_violation_strategy_cond_no_violation()
    test_sequence_strategy_skips_without_step_sequence()
    test_sequence_strategy_subsequence()
    test_sequence_strategy_exact()
    test_count_strategy()
    test_count_strategy_skips_without_b_columns()
    print("OK - tất cả test pass")
