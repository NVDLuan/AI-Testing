"""Note cho test case row — dành riêng cho flow bv_c (hand-washing), theo từng layer
của chain (`Sequence`/`Count`/`Violation`/`Trigger`, xem
`validator/hand_washing_strategy.py` + `tenants/factory.py::BvCFactory.create_validation_chain`).

Tách khỏi `report/notes_linfox.py` vì đây là logic ĐẶC THÙ bv_c (đọc cột B0..B6, Step
Sequence, Session Duration từ `gt.raw`, phân biệt No Trigger...) — không liên quan gì
tới flow linfox/EWS/karter_school. `report/template.py` chỉ gọi `is_handwashing()` để
chọn nhánh, không còn tự chứa logic 2 flow lẫn nhau.

Layer "Violation" là layer QUYẾT ĐỊNH pass/fail (`ViolationStrategy` chỉ so
`pred.has_violation` với `gt.expected_violation`, thuần boolean) — nhưng "expected" hiển
thị trên report giờ không chỉ có nhãn Violation/No Violation mà còn kèm count (B0..B6)
+ Session Duration để QC xem đủ ngữ cảnh (dữ liệu này sheet/JSON kết quả đã có sẵn,
không dùng để tính outcome). QC note (validation_note) của layer này rẽ theo predicted
Violation có KHỚP expected Violation không (outcome TP/TN = khớp, FN/FP = không khớp,
xem `_violation_notes`):

  KHỚP -> ghép 2 dòng ĐỘC LẬP, mỗi dòng chỉ hiện khi CÓ VẤN ĐỀ (đúng thì để trống, không
  note thừa); cả 2 đều trống thì gộp thành 1 câu "Đúng với kết quả kỳ vọng":
    1. "check logic output": đối chiếu output với CHÍNH count/duration mà model tự báo
       (không so GT) — model báo Violation (rửa tay KHÔNG đạt) thì PHẢI có ít nhất 1 chỉ
       số CHƯA đủ ngưỡng (count < `COUNT_THRESHOLD` ở bước nào đó, HOẶC session duration
       < `_DURATION_THRESHOLD_SEC`, có làm tròn) mới hợp lý; nếu CẢ 2 đều ĐỦ ngưỡng (số
       liệu cho thấy đã rửa tay đạt) mà vẫn báo Violation -> mâu thuẫn, note "sai logic
       code". Có căn cứ (đúng), hoặc No Violation -> để trống.
    2. So count từng bước ACTUAL (model báo) với EXPECTED (cột B0..B6 sheet) — lệch quá
       dung sai ±1 thì cảnh báo "Warning: count steps lệch nhiều"; trong dung sai (hoặc
       sheet không khai cột B) -> để trống.

  KHÔNG khớp (FN/FP) -> LUÔN "Sai kết quả mong muốn" (`_mismatch_note`), không xét
  count/duration — "sai logic code" CHỈ dành cho nhánh KHỚP ở trên (mismatch dù metrics
  có đủ ngưỡng hay không cũng coi là 1 lần đoán sai thông thường, không quy về lỗi logic
  cụ thể).
"""
from __future__ import annotations

import re

from report.notes_linfox import default_notes
from validator.hand_washing_strategy import COUNT_THRESHOLD

# Ngưỡng session duration (giây) cho dòng note 1 (check logic output). Chỉ dùng để HIỂN
# THỊ/cảnh báo trên report — KHÔNG ảnh hưởng outcome (đánh giá vẫn chỉ dựa cột Violation).
_DURATION_THRESHOLD_SEC = 30
_SESSION_DURATION_COL = "Session Duration"


def is_handwashing(gt) -> bool:
    """Nhận diện case bv_c: có Step Sequence hoặc nhãn 3-state (expected_triggered)."""
    return gt.expected_triggered is not None or "Step Sequence" in (gt.raw or {})


def _seq_str(seq) -> str:
    return ",".join(str(x) for x in (seq or []))


def _to_int(value) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def _b_counts(raw: dict) -> dict[str, int]:
    """Đọc count kỳ vọng từ các cột B0..B6 trong sheet -> {code: int}."""
    out: dict[str, int] = {}
    for k, v in (raw or {}).items():
        if re.match(r"^B\d+$", str(k).strip(), re.I):
            out[str(k).strip().upper()] = _to_int(v)
    return out


def _counts_kv(counts: dict) -> str:
    return ",".join(f"{k}:{counts[k]}" for k in sorted(counts))


def _expected_duration(gt) -> float | None:
    """Đọc Session Duration kỳ vọng từ sheet (cột `_SESSION_DURATION_COL`) -> giây."""
    raw = gt.raw or {}
    val = raw.get(_SESSION_DURATION_COL)
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def _actual_duration(pred) -> float | None:
    """Session duration model tự báo — lấy MAX qua các event (nhiều lần rửa tay -> lấy
    lần dài nhất), khớp cách `_merge_pred_counts` lấy max cho count."""
    if not (pred and pred.events):
        return None
    durations = [_to_int(ev.get("duration", 0)) for ev in pred.events]
    return max(durations) if durations else None


def _merge_pred_counts(pred, exp: dict) -> dict[str, int]:
    """Gộp count theo từng step qua các event (lấy max), căn theo các code expected
    (rỗng -> giữ nguyên mọi code model tự báo, không lọc theo GT)."""
    merged: dict[str, int] = {}
    for ev in pred.events:
        for code, c in (ev.get("counts") or {}).items():
            cu = str(code).strip().upper()
            merged[cu] = max(merged.get(cu, 0), _to_int(c))
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


def _violation_expect_actual_notes(gt, pred) -> tuple[str, str]:
    """expect/actual cho layer Violation: nhãn Violation/No Violation (tiêu chí THẬT SỰ
    dùng để tính outcome) + step count + session duration kèm theo CHỈ ĐỂ QC XEM (không
    tính vào outcome)."""
    exp_counts = _b_counts(gt.raw)
    exp_duration = _expected_duration(gt)
    expect_note = "Violation" if gt.expected_violation else "No Violation"
    if exp_counts:
        expect_note += f" - step count: {_counts_kv(exp_counts)}"
    if exp_duration is not None:
        expect_note += f" - session duration: {exp_duration:g}s"

    if pred and pred.events:
        actual_note = "Violation" if pred.has_violation else "No Violation"
        shown = _merge_pred_counts(pred, exp_counts)
        if shown:
            actual_note += f" - step count: {_counts_kv(shown)}"
        act_duration = _actual_duration(pred)
        if act_duration is not None:
            actual_note += f" - session duration: {act_duration:g}s"
    else:
        actual_note = "No Violation"
    return expect_note, actual_note


def _metrics_look_compliant(pred) -> tuple[bool, dict[str, int], int | None]:
    """Áp luật ngưỡng đơn giản lên CHÍNH số liệu model tự báo (không so GT): mọi step
    count >= COUNT_THRESHOLD VÀ session duration (làm tròn) >= _DURATION_THRESHOLD_SEC
    -> coi là "đã rửa tay đạt" (compliant). Trả kèm actual_counts/duration_rounded để
    nơi gọi build message chi tiết, khỏi tính lại."""
    actual_counts = _merge_pred_counts(pred, {})
    counts_sufficient = bool(actual_counts) and all(c >= COUNT_THRESHOLD for c in actual_counts.values())
    duration = _actual_duration(pred)
    duration_rounded = round(duration) if duration is not None else None
    duration_sufficient = duration_rounded is not None and duration_rounded >= _DURATION_THRESHOLD_SEC
    return (counts_sufficient and duration_sufficient), actual_counts, duration_rounded


def _logic_check_note(pred) -> str:
    """Dòng 1 QC note (chỉ khi 2 trạng thái Violation predicted/expected KHỚP nhau —
    xem `_violation_notes`): model báo Violation (rửa tay KHÔNG đạt) mà số liệu CHÍNH nó
    tự báo lại "đã đạt" (`_metrics_look_compliant` — mọi step count đủ ngưỡng VÀ duration
    đủ ngưỡng, bằng chứng RÕ RÀNG cho "không vi phạm") -> mâu thuẫn, lỗi logic phía code
    phân tích. Chỉ áp dụng khi output = Violation; metrics KHÔNG compliant (có căn cứ,
    dù chỉ 1 chỉ số chưa đủ) hoặc No Violation đều trả rỗng — không cần note khi không
    có vấn đề gì."""
    if not (pred and pred.has_violation):
        return ""
    compliant, actual_counts, duration_rounded = _metrics_look_compliant(pred)
    if not compliant:
        return ""  # có căn cứ (count hoặc duration chưa đủ ngưỡng) -> không cần note

    dur_txt = f"{duration_rounded}s" if duration_rounded is not None else "không rõ"
    return (
        f"Check logic: sai logic code (violation nhưng count đủ ngưỡng >= {COUNT_THRESHOLD} "
        f"({_counts_kv(actual_counts)}) và duration đủ ngưỡng >= {_DURATION_THRESHOLD_SEC}s "
        f"({dur_txt}) — số liệu cho thấy đã rửa tay đạt, không có căn cứ báo violation)"
    )


def _mismatch_note() -> str:
    """QC note khi predicted Violation KHÁC expected Violation (FN/FP): LUÔN "Sai kết
    quả mong muốn" — không xét metrics (count/duration). "sai logic code" chỉ dành cho
    nhánh KHỚP (`_logic_check_note`: predicted=Violation nhưng tự mâu thuẫn với CHÍNH số
    liệu nó báo); mismatch (dù metrics có đủ ngưỡng hay không) đều coi là 1 lần đoán sai
    thông thường, không quy về lỗi logic cụ thể."""
    return "Sai kết quả mong muốn"


def _count_deviation_note(gt, pred) -> str:
    """Dòng 2 QC note: so count từng bước ACTUAL (model báo) với EXPECTED (cột B0..B6
    sheet) — lệch quá dung sai ±1 (|actual-expected| > 1) ở bất kỳ step nào thì cảnh báo;
    trong dung sai (hoặc sheet không khai cột B) thì trả rỗng — không cần note."""
    expected = _b_counts(gt.raw)
    if not expected:
        return ""
    actual = _merge_pred_counts(pred, expected) if (pred and pred.events) else {c: 0 for c in expected}
    over = {
        code: (actual.get(code, 0) - expected[code])
        for code in expected
        if abs(actual.get(code, 0) - expected[code]) > 1
    }
    if not over:
        return ""  # trong dung sai -> không cần note
    detail = ", ".join(f"{code} exp={expected[code]} act={actual.get(code, 0)}" for code in sorted(over))
    return f"Warning: count steps lệch nhiều ({detail})"


def _violation_notes(gt, pred, outcome: str) -> tuple[str, str, str]:
    """(expect_note, actual_note, validation_note) cho layer Violation.

    Bước 1: predicted Violation có KHỚP expected Violation không (`outcome` TP/TN =
    khớp — cùng phép so sánh với `_confusion()` trong validator, tái dùng luôn cho nhất
    quán, không tính lại).
      - KHỚP -> validation_note ghép 2 dòng độc lập (check logic output + count
        deviation), bỏ dòng nào ĐÚNG (rỗng); CẢ 2 đều đúng thì gộp thành 1 câu chung
        "Đúng với kết quả kỳ vọng".
      - KHÔNG khớp (FN/FP) -> xem `_mismatch_note`.
    """
    expect_note, actual_note = _violation_expect_actual_notes(gt, pred)
    if outcome in ("TP", "TN"):
        lines = [line for line in (_logic_check_note(pred), _count_deviation_note(gt, pred)) if line]
        validation_note = " - ".join(lines) if lines else "Đúng với kết quả kỳ vọng"
    else:
        validation_note = _mismatch_note()
    return expect_note, actual_note, validation_note


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


def handwashing_notes(gt, pred, strategy: str, outcome: str) -> tuple[str, str, str]:
    """(expect_note, actual_note, validation_note) cho 1 dòng test case bv_c.

    Batch = 1 layer (report.strategy) -> note đúng tiêu chí của layer đó. Violation ->
    xem `_violation_notes` (2 dòng QC note riêng). Sequence -> Step Sequence. Count ->
    count từng bước. Trigger dùng note mặc định theo outcome — không có tiêu chí phụ để
    nói. No Trigger (GT không cần trigger, hoặc model không trigger) luôn ghi đè, bỏ qua
    note step/sequence/count/violation.
    """
    if strategy == "Violation":
        expect_note, actual_note, validation_note = _violation_notes(gt, pred, outcome)
    else:
        expect_note, actual_note, validation_note = default_notes(outcome)
        if strategy == "Sequence":
            expect_note, actual_note = _sequence_notes(gt, pred)
        elif strategy == "Count":
            expect_note, actual_note = _count_notes(gt, pred)

    if gt.expected_triggered is False:
        expect_note = "No Trigger"
    if not (pred and pred.events):
        actual_note = "No Trigger"
    return expect_note, actual_note, validation_note
