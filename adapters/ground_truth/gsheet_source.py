"""Nguồn ground truth từ Google Sheet.

Mỗi job trỏ tới 1 spreadsheet (job.sheet_id); mỗi rule là 1 tab (rule.sheet).
Hai cách đọc:
    - GoogleSheetGroundTruthSource: sheet RIÊNG TƯ, cần `gspread` + service account JSON.
    - PublicGoogleSheetGroundTruthSource: sheet PUBLIC ("ai có link đều xem"), đọc thẳng
      qua endpoint export CSV của Google, chỉ cần `requests` — không cần credential.
"""
from __future__ import annotations

import csv
import io
from urllib.parse import quote

from configs.schema import JobDef, RuleDef
from interfaces.ground_truth import GroundTruthSource
from validator.models import GroundTruthRecord

from .rows import row_to_record

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


class PublicGoogleSheetGroundTruthSource(GroundTruthSource):
    """Đọc Google Sheet PUBLIC qua export CSV — không cần credential.

    Yêu cầu sheet để chế độ "Anyone with the link: Viewer" (hoặc đã publish).
    Dùng endpoint gviz: trả CSV theo TÊN tab (rule.sheet), hàng đầu là header.
    """

    def __init__(self, columns: dict[str, str] | None = None, timeout_sec: float = 30.0) -> None:
        self._columns = columns or DEFAULT_COLUMNS
        self._timeout_sec = timeout_sec

    @staticmethod
    def _csv_url(sheet_id: str, tab: str) -> str:
        base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
        return f"{base}&sheet={quote(tab)}" if tab else base

    def load(self, job: JobDef, rule: RuleDef) -> list[GroundTruthRecord]:
        import requests  # import hoãn để offline không cần cài

        if not job.sheet_id:
            raise ValueError(f"job {job.name!r} thiếu sheet_id để đọc Google Sheet public")
        url = self._csv_url(job.sheet_id, rule.sheet)
        resp = requests.get(url, timeout=self._timeout_sec)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        records: list[GroundTruthRecord] = []
        for row in reader:
            rec = row_to_record(row, self._columns, rule)
            if rec is not None:
                records.append(rec)
        return records
