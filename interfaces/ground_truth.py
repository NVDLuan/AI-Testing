"""Interface nguồn ground truth (Google Sheet, fake/offline...)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from configs.schema import JobDef, RuleDef
from validator.models import GroundTruthRecord


class GroundTruthSource(ABC):
    @abstractmethod
    def load(self, job: JobDef, rule: RuleDef) -> list[GroundTruthRecord]:
        """Đọc ground truth cho 1 (job, rule) — 1 bản ghi = 1 video.

        Với Google Sheet: đọc tab `rule.sheet` trong spreadsheet `job.sheet_id`.
        """
        raise NotImplementedError
