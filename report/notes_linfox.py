"""Note mặc định cho test case row — dùng cho flow linfox/EWS/karter_school (binary
violation, không có tiêu chí phụ như Sequence/Count/Violation&cond) và cũng là NỀN
(fallback) mà `report.notes_handwashing` build tiếp lên trên cho các nhánh chưa có
override riêng (vd outcome TP/TN, layer Trigger).
"""
from __future__ import annotations

# outcome -> (expect_note, actual_note, validation_note)
_NOTE_BY_OUTCOME: dict[str, tuple[str, str, str]] = {
    "TP": (
        "Violation occurred",
        "Violation detected",
        "AI correctly detected the violation",
    ),
    "TN": (
        "No violation occurred",
        "No violation detected",
        "AI correctly determined that no violation occurred",
    ),
    "FN": (
        "Violation occurred",
        "Violation not detected",
        "AI missed the violation",
    ),
    "FP": (
        "No violation occurred",
        "Violation detected",
        "AI incorrectly reported a violation",
    ),
}


def default_notes(outcome: str) -> tuple[str, str, str]:
    """(expect_note, actual_note, validation_note) mặc định theo outcome (TP/FP/FN/TN/MISSING)."""
    return _NOTE_BY_OUTCOME.get(outcome, ("", "", ""))
