"""Nguồn ground truth từ Google Sheet.

Mỗi job trỏ tới 1 spreadsheet (job.sheet_id); mỗi rule là 1 tab (rule.sheet).
Cần `gspread` + service account JSON (import hoãn để chạy offline không cần cài).
"""
from __future__ import annotations

from configs.schema import JobDef, RuleDef
from interfaces.ground_truth import GroundTruthSource
from validator.models import GroundTruthRecord

from ._rows import row_to_record

# Map tên cột logic -> tên cột thật trên sheet. Chỉnh cho khớp sheet của bạn.
DEFAULT_COLUMNS = {
    "video_url": "path_video",   # nguồn S3 để download
    "video_name": "video_name",  # unique trong rule
    "expected": "expected",      # nhãn ground truth
}


class GoogleSheetGroundTruthSource(GroundTruthSource):
    def __init__(self, credentials_file: str = "", columns: dict[str, str] | None = None) -> None:
        self._credentials_file = credentials_file
        self._columns = columns or DEFAULT_COLUMNS
        self._client = None

    def _gc(self):
        if self._client is None:
            try:
                import gspread  # noqa: WPS433
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("Cần cài gspread + google-auth để đọc Google Sheet") from exc
            self._client = gspread.service_account(filename=self._credentials_file or None)
        return self._client

    def load(self, job: JobDef, rule: RuleDef) -> list[GroundTruthRecord]:
        sh = self._gc().open_by_key(job.sheet_id)
        ws = sh.worksheet(rule.sheet) if rule.sheet else sh.sheet1
        records: list[GroundTruthRecord] = []
        for row in ws.get_all_records():
            rec = row_to_record(row, self._columns, rule)
            if rec is not None:
                records.append(rec)
        return records
