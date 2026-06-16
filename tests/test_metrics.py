"""Test phần lõi: confusion matrix + validate. Chạy: python -m tests.test_metrics"""
from validator.metrics import ConfusionMatrix
from validator.models import GroundTruthRecord, Prediction
from validator.validator import validate


def test_confusion_matrix_metrics():
    cm = ConfusionMatrix()
    # 3 dương: 2 đúng (TP), 1 sót (FN)
    cm.add(True, True)
    cm.add(True, True)
    cm.add(True, False)
    # 3 âm: 1 báo nhầm (FP), 2 đúng (TN)
    cm.add(False, True)
    cm.add(False, False)
    cm.add(False, False)

    assert (cm.tp, cm.fp, cm.fn, cm.tn) == (2, 1, 1, 2)
    assert abs(cm.recall - 2 / 3) < 1e-9          # TP/(TP+FN)
    assert abs(cm.far - 1 / 3) < 1e-9             # FP/(FP+TN)
    assert abs(cm.precision - 2 / 3) < 1e-9


def test_validate_outcomes_and_missing():
    gt = [
        GroundTruthRecord("v1", "u1", True, "r1", "c1"),
        GroundTruthRecord("v2", "u2", False, "r1", "c1"),
        GroundTruthRecord("v3", "u3", True, "r1", "c1"),   # thiếu kết quả
    ]
    preds = {
        "v1": Prediction("v1", True),    # TP
        "v2": Prediction("v2", True),    # FP
    }
    rep = validate(gt, preds)
    outcomes = {c.video_id: c.outcome for c in rep.comparisons}
    assert outcomes == {"v1": "TP", "v2": "FP", "v3": "MISSING"}
    assert len(rep.missing) == 1
    assert rep.overall.total == 2  # video MISSING không tính vào matrix


def test_empty_safe():
    cm = ConfusionMatrix()
    assert cm.recall == 0.0 and cm.far == 0.0 and cm.f1 == 0.0


if __name__ == "__main__":
    test_confusion_matrix_metrics()
    test_validate_outcomes_and_missing()
    test_empty_safe()
    print("OK - tất cả test pass")
