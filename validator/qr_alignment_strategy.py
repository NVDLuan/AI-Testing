"""Luật chấm theo QR frame_id: evidence có rơi ĐÚNG khoảng thời gian vi phạm không.

Video nguồn được nhúng QR = số thứ tự frame (xem `qr_video_tagger`, `--embed-qr`), nên clip
evidence mang sẵn frame gốc. `pipeline.handlers.ReadEvidenceQRHandler` đọc QR frame đầu/cuối
của từng clip rồi gắn vào event (`EVENT_QR_FIRST` / `EVENT_QR_LAST`); strategy này chỉ đọc
các field đó (thuần, không I/O) và so với khoảng vi phạm khai trên sheet.

Khoảng vi phạm kỳ vọng: cột `ExpectedResult` (giống repo cũ) — JSON/list các
`{"eventStart": "MM:SS", "eventEnd": "MM:SS", "area": [...]}`; đổi sang frame theo `fps`.

Outcome (1 video):
    GT không vi phạm : có evidence -> FP, không -> TN (giống binary, không cần QR)
    GT vi phạm       : không evidence -> FN
                       có evidence, khoảng QR giao 1 khoảng kỳ vọng (± tolerance) -> TP
                       có evidence nhưng KHÔNG clip nào giao -> ["FN", "FP"]
                         (bỏ sót vi phạm thật + báo sai thời điểm)
                       không verify được (sheet thiếu ExpectedResult / không clip nào đọc
                         được QR) -> TP như binary, `detail.qr_status` ghi rõ lý do
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

from .models import GroundTruthRecord, Prediction
from .strategies import ValidationStrategy, _confusion

EVENT_QR_FIRST = "qr_first_frame"
EVENT_QR_LAST = "qr_last_frame"

# qr_status trong detail
QR_MATCHED = "matched"
QR_OUT_OF_WINDOW = "evidence_out_of_window"
QR_NO_EXPECTED_WINDOW = "no_expected_window"
QR_NO_QR_IN_EVIDENCE = "no_qr_in_evidence"
QR_NOT_APPLICABLE = "not_applicable"

_TIME_RE = re.compile(r"^\d+(:\d+){0,2}(\.\d+)?$")


def time_to_seconds(value: Any) -> float | None:
    """'SS' | 'MM:SS' | 'HH:MM:SS' (cho phép phần thập phân) hoặc số -> giây; sai định dạng -> None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value or "").strip()
    if not _TIME_RE.match(s):
        return None
    seconds = 0.0
    for part in s.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def _load_structured(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(s)
        except (ValueError, SyntaxError):
            continue
    return None


def parse_expected_windows(value: Any, fps: float) -> list[tuple[int, int]]:
    """Cột ExpectedResult -> list (start_frame, end_frame), sort tăng dần; bỏ entry hỏng."""
    data = _load_structured(value)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    windows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        start = time_to_seconds(item.get("eventStart"))
        end = time_to_seconds(item.get("eventEnd"))
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        windows.append((int(round(start * fps)), int(round(end * fps))))
    return sorted(windows)


def evidence_qr_ranges(pred: Prediction) -> list[tuple[int, int]]:
    """Khoảng frame gốc của từng clip evidence đọc được QR (bỏ clip không đọc được)."""
    ranges = []
    for ev in pred.events or []:
        if not isinstance(ev, dict):
            continue
        first, last = ev.get(EVENT_QR_FIRST), ev.get(EVENT_QR_LAST)
        if isinstance(first, int) and isinstance(last, int):
            ranges.append((min(first, last), max(first, last)))
    return ranges


class QRAlignmentStrategy(ValidationStrategy):
    """Chấm vi phạm có kiểm tra THỜI ĐIỂM bằng QR frame_id trong clip evidence."""

    name = "QR Alignment"

    def __init__(self, fps: float = 25.0, tolerance_sec: float = 1.0,
                 expected_field: str = "ExpectedResult") -> None:
        if fps <= 0:
            raise ValueError("fps phải > 0")
        self.fps = fps
        self.tolerance_frames = max(int(round(tolerance_sec * fps)), 0)
        self.expected_field = expected_field

    # ------------------------------------------------------------------ core
    def evaluate(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any]:
        has_evidence = bool(pred.has_violation)
        windows = parse_expected_windows(gt.raw.get(self.expected_field), self.fps)
        ranges = evidence_qr_ranges(pred)
        result: dict[str, Any] = {
            "expected_windows": windows,
            "evidence_qr_ranges": ranges,
            "evidence_count": len(pred.events or []),
            "fps": self.fps,
            "tolerance_frames": self.tolerance_frames,
            "matches": [],
        }

        if not gt.expected_violation or not has_evidence:
            result["qr_status"] = QR_NOT_APPLICABLE
            result["outcomes"] = [_confusion(gt.expected_violation, has_evidence)]
            return result
        if not windows:
            result["qr_status"] = QR_NO_EXPECTED_WINDOW
            result["outcomes"] = ["TP"]
            return result
        if not ranges:
            result["qr_status"] = QR_NO_QR_IN_EVIDENCE
            result["outcomes"] = ["TP"]
            return result

        tol = self.tolerance_frames
        matches = [
            {"evidence": list(r), "window": list(w)}
            for r in ranges for w in windows
            if r[0] <= w[1] + tol and w[0] - tol <= r[1]
        ]
        result["matches"] = matches
        if matches:
            result["qr_status"] = QR_MATCHED
            result["outcomes"] = ["TP"]
        else:
            result["qr_status"] = QR_OUT_OF_WINDOW
            result["outcomes"] = ["FN", "FP"]
        return result

    # ------------------------------------------------------------- contract
    def classify(self, gt: GroundTruthRecord, pred: Prediction) -> list[str]:
        return self.evaluate(gt, pred)["outcomes"]

    def expected_label(self, gt: GroundTruthRecord) -> bool | str:
        return gt.expected_violation

    def predicted_label(self, gt: GroundTruthRecord, pred: Prediction) -> bool | str:
        if not pred.has_violation:
            return False
        status = self.evaluate(gt, pred)["qr_status"]
        return {
            QR_MATCHED: "Violation (đúng thời điểm)",
            QR_OUT_OF_WINDOW: "Violation (sai thời điểm)",
        }.get(status, True)

    def detail(self, gt: GroundTruthRecord, pred: Prediction) -> dict[str, Any] | None:
        res = self.evaluate(gt, pred)
        res.pop("outcomes", None)
        res["expected_windows_sec"] = [
            [round(s / self.fps, 2), round(e / self.fps, 2)] for s, e in res["expected_windows"]
        ]
        res["evidence_qr_ranges_sec"] = [
            [round(s / self.fps, 2), round(e / self.fps, 2)] for s, e in res["evidence_qr_ranges"]
        ]
        res["expected_windows"] = [list(w) for w in res["expected_windows"]]
        res["evidence_qr_ranges"] = [list(r) for r in res["evidence_qr_ranges"]]
        return res
