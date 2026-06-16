"""Nguồn ground truth offline — sinh dữ liệu giả để chạy end-to-end không cần mạng.

Dùng chính các camera_id thật nạp từ <CODE>_config.json để dữ liệu sát thực tế.
Nhãn expected sinh deterministic theo seed (ổn định giữa các lần chạy).
"""
from __future__ import annotations

import random

from configs.schema import JobDef, RuleDef
from interfaces.ground_truth import GroundTruthSource
from validator.models import GroundTruthRecord


class FakeSheetGroundTruthSource(GroundTruthSource):
    def __init__(self, videos_per_camera: int = 2, positive_ratio: float = 0.5, seed: int = 1) -> None:
        self._n = videos_per_camera
        self._positive_ratio = positive_ratio
        self._seed = seed

    def load(self, job: JobDef, rule: RuleDef) -> list[GroundTruthRecord]:
        rng = random.Random(f"{self._seed}-{job.name}-{rule.code}")
        camera_ids = list(rule.cameras.keys()) or [f"{rule.code}-cam-01", f"{rule.code}-cam-02"]
        records: list[GroundTruthRecord] = []
        for cam_id in camera_ids:
            for i in range(self._n):
                name = f"{cam_id}_{i:02d}"          # video_name (unique trong rule)
                records.append(
                    GroundTruthRecord(
                        video_id=f"{rule.code or rule.name}/{name}",
                        video_url=f"s3://fake-bucket/{job.name}/{name}.mp4",
                        expected_violation=rng.random() < self._positive_ratio,
                        rule_id=rule.name,
                        camera_id=cam_id,
                        video_name=name,
                        raw={"source": "fake"},
                    )
                )
        return records
