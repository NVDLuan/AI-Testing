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
        # video_id -> (expected_triggered | None, expected_violation)
        self._oracle: dict[str, tuple[bool | None, bool]] = {}
        self._uploaded: set[str] = set()

    def prime(self, ground_truth: list[GroundTruthRecord]) -> None:
        self._oracle.update(
            {r.video_id: (r.expected_triggered, r.expected_violation) for r in ground_truth}
        )

    def check_missing(self, videos: list[VideoRef]) -> list[VideoRef]:
        return [v for v in videos if v.video_id not in self._uploaded]

    def upload(self, video: VideoRef, content: bytes | None = None, filename: str = "") -> None:
        self._uploaded.add(video.video_id)

    def trigger(self, videos: list[VideoRef], rule: RuleDef, batch_code: str) -> str:
        return f"mock-job-{rule.code or rule.name}-{len(videos)}"

    def _noisy(self, truth: bool) -> bool:
        """Tiêm lỗi: truth -> giữ trừ khi miss; not truth -> báo nhầm theo far."""
        if truth:
            return self._rng.random() >= self._miss_rate
        return self._rng.random() < self._far_rate

    def poll_results(self, job_id: str, videos: list[VideoRef]) -> dict[str, Prediction]:
        results: dict[str, Prediction] = {}
        for v in videos:
            exp_trig, exp_viol = self._oracle.get(v.video_id, (None, False))

            if exp_trig is None:
                # Tenant binary: chỉ mô phỏng nhãn vi phạm.
                predicted = self._noisy(exp_viol)
                triggered = None
                has_violation = predicted
            else:
                # Tenant 3-state (bv_c): mô phỏng cả trigger lẫn vi phạm.
                triggered = self._noisy(exp_trig)
                # Vi phạm chỉ có nghĩa khi đã trigger; nếu không trigger thì coi như không vi phạm.
                has_violation = self._noisy(exp_viol) if triggered else False
                predicted = has_violation

            conf = self._rng.uniform(0.6, 0.99) if predicted else self._rng.uniform(0.0, 0.4)
            results[v.video_id] = Prediction(
                video_id=v.video_id,
                has_violation=has_violation,
                confidence=round(conf, 3),
                events=[{"type": v.rule_id, "score": round(conf, 3)}] if predicted else [],
                triggered=triggered,
                raw={"job_id": job_id, "simulated": True},
            )
        return results
