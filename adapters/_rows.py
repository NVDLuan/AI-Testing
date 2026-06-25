"""Tiện ích dùng chung: map dòng dữ liệu thô -> GroundTruthRecord."""
from __future__ import annotations

from typing import Any

from configs.schema import RuleDef
from validator.models import GroundTruthRecord

# Giá trị được coi là "có vi phạm" (không phân biệt hoa thường).
_TRUE_TOKENS = {
    "1", "true", "yes", "y", "co", "có", "violation", "vi phạm", "fail", "positive", "p",
    "approve",
}


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUE_TOKENS


def parse_trigger_status(value: Any) -> tuple[bool, bool]:
    """Phân tích cột "Expected Status" của bv_c -> (triggered, violation).

    Vocabulary thật trên sheet (và biến thể khoan dung):
        "No trigger"             -> (False, False)  # hệ thống KHÔNG nên trigger
        "No violation"           -> (True,  False)  # có trigger nhưng đúng (không vi phạm)
        "Violation"              -> (True,  True)   # có trigger và vi phạm
        ("Trigger (No Violation)"/"Trigger (Violation)" cũng nhận đúng)

    Quy tắc: "no trigger"/rỗng -> không trigger. Còn lại -> đã trigger; có vi phạm khi
    có chữ "violation" mà KHÔNG phải "no violation".
    """
    s = str(value or "").strip().lower()
    if not s or "no trigger" in s or s.replace(" ", "") == "notrigger":
        return (False, False)
    violation = "violation" in s and "no violation" not in s
    return (True, violation)


def make_video_id(rule: RuleDef, video_name: str) -> str:
    """Khoá nội bộ unique toàn cục: rule_code + video_name (vì name chỉ unique trong rule)."""
    return f"{rule.code or rule.name}/{video_name}"


def row_to_record(
    row: dict[str, Any], columns: dict[str, str], rule: RuleDef
) -> GroundTruthRecord | None:
    """Chuyển 1 dòng (dict theo tên cột thật) thành GroundTruthRecord.

    `columns` map tên logic -> tên cột thật:
        video_url (path_video, bắt buộc), video_name (bắt buộc),
        camera (tuỳ chọn), và MỘT trong hai cách gắn nhãn:
          - binary: `expected` (cột nhãn vi phạm boolean), hoặc
          - 3-state: `trigger_status` (cột "Expected Status" của bv_c). Khi có key này,
            cột được phân tích thành (expected_triggered, expected_violation).
    """
    url_col = columns.get("video_url", "path_video")
    name_col = columns.get("video_name", "video_name")
    exp_col = columns.get("expected", "expected")
    cam_col = columns.get("camera", "")
    trig_col = columns.get("trigger_status", "")

    url = str(row.get(url_col, "")).strip()
    video_name = str(row.get(name_col, "")).strip()
    if not url or not video_name:
        return None

    if trig_col:
        triggered, violation = parse_trigger_status(row.get(trig_col))
        expected_triggered: bool | None = triggered
        expected_violation = violation
    else:
        expected_triggered = None
        expected_violation = parse_bool(row.get(exp_col))

    return GroundTruthRecord(
        video_id=make_video_id(rule, video_name),
        video_url=url,
        expected_violation=expected_violation,
        rule_id=rule.name,
        camera_id=str(row.get(cam_col, "")).strip() if cam_col else "",
        video_name=video_name,
        expected_triggered=expected_triggered,
        raw=dict(row),
    )
