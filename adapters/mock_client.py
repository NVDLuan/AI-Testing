"""MockAnalysisClient — giả lập bên thứ 3 để chạy end-to-end khi chưa có API thật.

Sau khi `prime(ground_truth)`, mock sinh prediction từ nhãn thật rồi CHỦ ĐỘNG
tiêm lỗi theo tỉ lệ:
    far_rate  : tỉ lệ video không vi phạm bị báo nhầm (-> FP)
    miss_rate : tỉ lệ video vi phạm bị bỏ sót        (-> FN)
Chỉ là công cụ mô phỏng; KHÔNG phản ánh độ chính xác hệ thống thật.
"""
from __future__ import annotations

import random

from configs.schema import RuleDef
from interfaces.analysis_client import AnalysisClient
from validator.models import GroundTruthRecord, Prediction, VideoRef


class MockAnalysisClient(AnalysisClient):
    def __init__(self, far_rate: float = 0.1, miss_rate: float = 0.1, seed: int = 42) -> None:
        self._far_rate = far_rate
        self._miss_rate = miss_rate
        self._rng = random.Random(seed)
        self._oracle: dict[str, bool] = {}
        self._uploaded: set[str] = set()

    def prime(self, ground_truth: list[GroundTruthRecord]) -> None:
        self._oracle.update({r.video_id: r.expected_violation for r in ground_truth})

    def check_missing(self, videos: list[VideoRef]) -> list[VideoRef]:
        return [v for v in videos if v.video_id not in self._uploaded]

    def upload(self, video: VideoRef, file_path: str | None = None) -> None:
        self._uploaded.add(video.video_id)

    def trigger(self, videos: list[VideoRef], rule: RuleDef) -> str:
        return f"mock-job-{rule.code or rule.name}-{len(videos)}"

    def poll_results(self, job_id: str, videos: list[VideoRef]) -> dict[str, Prediction]:
        results: dict[str, Prediction] = {}
        for v in videos:
            truth = self._oracle.get(v.video_id, False)
            if truth:
                predicted = self._rng.random() >= self._miss_rate
            else:
                predicted = self._rng.random() < self._far_rate
            conf = self._rng.uniform(0.6, 0.99) if predicted else self._rng.uniform(0.0, 0.4)
            results[v.video_id] = Prediction(
                video_id=v.video_id,
                has_violation=predicted,
                confidence=round(conf, 3),
                events=[{"type": v.rule_id, "score": round(conf, 3)}] if predicted else [],
                raw={"job_id": job_id, "simulated": True},
            )
        return results
