"""Note cho test case row — dành riêng cho flow bv_c (hand-washing), theo từng layer
của chain (`Sequence`/`Count`/`Violation & cond`/`Trigger`, xem
`validator/hand_washing_strategy.py` + `tenants/factory.py::BvCFactory.create_validation_chain`).

Tách khỏi `report/notes_linfox.py` vì đây là logic ĐẶC THÙ bv_c (đọc cột B0..B6, Step
Sequence từ `gt.raw`, phân biệt No Trigger...) — không liên quan gì tới flow
linfox/EWS/karter_school. `report/template.py` chỉ gọi `is_handwashing()` để chọn nhánh,
không còn tự chứa logic 2 flow lẫn nhau.
"""
from __future__ import annotations

import re

from report.notes_linfox import default_notes


def is_handwashing(gt) -> bool:
    """Nhận diện case bv_c: có Step Sequence hoặc nhãn 3-state (expected_triggered)."""
    return gt.expected_triggered is not None or "Step Sequence" in (gt.raw or {})


def _seq_str(seq) -> str:
    return ",".join(str(x) for x in (seq or []))


def _b_counts(raw: dict) -> dict[str, int]:
    """Đọc count kỳ vọng từ các cột B0..B6 trong sheet -> {code: int}."""
    out: dict[str, int] = {}
    for k, v in (raw or {}).items():
        if re.match(r"^B\d+$", str(k).strip(), re.I):
            try:
                out[str(k).strip().upper()] = int(float(str(v).strip()))
            except (ValueError, TypeError):
                out[str(k).strip().upper()] = 0
    return out


def _counts_kv(counts: dict) -> str:
    return ",".join(f"{k}:{counts[k]}" for k in sorted(counts))


def _merge_pred_counts(pred, exp: dict) -> dict[str, int]:
    """Gộp count theo từng step qua các event (lấy max), căn theo các code expected."""
    merged: dict[str, int] = {}
    for ev in pred.events:
        for code, c in (ev.get("counts") or {}).items():
            cu = str(code).strip().upper()
            try:
                c = int(float(str(c)))
            except (ValueError, TypeError):
                c = 0
            merged[cu] = max(merged.get(cu, 0), c)
    return {code: merged.get(code, 0) for code in exp} if exp else merged


def _sequence_notes(gt, pred) -> tuple[str, str]:
    """Note theo layer Sequence: 'Sequence step: <a,b,c>' cho expected & actual."""
    exp_seq = [int(x) for x in re.findall(r"\d+", str((gt.raw or {}).get("Step Sequence", "")))]
    expect_note = f"Sequence step: {_seq_str(exp_seq)}"

    if pred and pred.events:
        seqs = [ev.get("sequence", []) for ev in pred.events if ev.get("sequence")]
        act_seq = " | ".join(_seq_str(s) for s in seqs)  # nhiều event -> ngăn bằng ' | '
    else:
        act_seq = ""
    actual_note = f"Sequence step: {act_seq}"
    return expect_note, actual_note


def _count_notes(gt, pred) -> tuple[str, str]:
    """Note theo layer Count: 'Violation: <T/F> - step count: B1:5,B2:3,...' cho expected & actual."""
    exp = _b_counts(gt.raw)
    expect_note = f"Violation: {bool(gt.expected_violation)} - step count: {_counts_kv(exp)}"

    if pred and pred.events:
        shown = _merge_pred_counts(pred, exp)
        actual_note = f"Violation: {bool(pred.has_violation)} - step count: {_counts_kv(shown)}"
    else:
        actual_note = "Violation: False - step count: "
    return expect_note, actual_note


def _violation_cond_notes(gt, pred) -> tuple[str, str]:
    """Note cho layer Violation & cond: nhãn Violation/No Violation + cond (count) đang
    check — cond đổi theo GT: No Violation -> count >= 5; Violation -> count lệch expected <= 1.
    """
    exp_counts = _b_counts(gt.raw)
    exp_label = "Violation" if gt.expected_violation else "No Violation"
    cond_desc = "count lệch expected <= 1" if gt.expected_violation else "count >= 5"
    expect_note = f"{exp_label} - cond: {cond_desc}"
    if exp_counts:
        expect_note += f" - step count: {_counts_kv(exp_counts)}"

    if pred and pred.events:
        act_label = "Violation" if pred.has_violation else "No Violation"
        shown = _merge_pred_counts(pred, exp_counts)
        actual_note = f"{act_label} - step count: {_counts_kv(shown)}" if shown else act_label
    else:
        actual_note = "No Violation"
    return expect_note, actual_note


def _violation_cond_reason_note(comp) -> str | None:
    """QC note cho FN/FP ở layer Violation & cond — phân biệt LÝ DO sai theo đúng event
    (`comp.event_index`) trong `comp.detail['events']` (gắn bởi `ViolationStrategy.detail()`):
        - violation flag sai (không khớp GT)      -> "nhận diện sai vi phạm"
        - violation flag khớp nhưng cond (count) sai -> "nhận diện thiếu số lần rửa tay"
    None nếu không xác định được (thiếu detail/event) -> giữ note mặc định theo outcome.
    """
    detail = comp.detail or {}
    events = detail.get("events") or []
    if comp.event_index >= len(events):
        return None
    ev = events[comp.event_index]
    violation_match = bool(ev.get("violation")) == bool(detail.get("expected_violation"))
    if not violation_match:
        return "incorrect identification of violation"
    if not ev.get("cond_ok", True):
        return "The detected handwashing steps are fewer than required"
    return None


def handwashing_notes(gt, comp, pred, strategy: str, outcome: str) -> tuple[str, str, str]:
    """(expect_note, actual_note, validation_note) cho 1 dòng test case bv_c.

    Batch = 1 layer (report.strategy) -> note đúng tiêu chí của layer đó (Sequence ->
    Step Sequence, Count -> count từng bước, Violation & cond -> nhãn Violation/No
    Violation + cond count đang check). Trigger dùng note mặc định theo outcome — không
    có tiêu chí sequence/count/violation riêng để nói. No Trigger (GT không cần trigger,
    hoặc model không trigger) luôn ghi đè, bỏ qua note step/sequence/count.
    """
    expect_note, actual_note, validation_note = default_notes(outcome)

    if strategy == "Sequence":
        expect_note, actual_note = _sequence_notes(gt, pred)
    elif strategy == "Count":
        expect_note, actual_note = _count_notes(gt, pred)
    elif strategy == "Violation & cond":
        expect_note, actual_note = _violation_cond_notes(gt, pred)
        if comp is not None and outcome in ("FN", "FP"):
            reason_note = _violation_cond_reason_note(comp)
            if reason_note:
                validation_note = reason_note

    if gt.expected_triggered is False:
        expect_note = "No Trigger"
    if not (pred and pred.events):
        actual_note = "No Trigger"
    return expect_note, actual_note, validation_note
