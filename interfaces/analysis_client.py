"""Interface tới hệ thống phân tích video của bên thứ 3.

Mọi tích hợp thật chỉ cần implement abstract class này; pipeline không quan
tâm đằng sau là HTTP, gRPC hay SDK gì.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from configs.schema import RuleDef
from validator.models import GroundTruthRecord, Prediction, VideoRef


@dataclass
class HealthStatus:
    healthy: bool
    detail: dict[str, Any] = field(default_factory=dict)


class AnalysisClient(ABC):
    """Vòng đời: health_check -> prime -> check_missing -> upload -> trigger -> poll_results."""

    def health_check(self) -> HealthStatus:
        """Kiểm tra sức khoẻ hệ thống trước khi test. Mặc định coi như khoẻ."""
        return HealthStatus(True, {"note": "no health check implemented"})

    def prime(self, ground_truth: list[GroundTruthRecord]) -> None:
        """Hook tuỳ chọn: nạp dữ liệu trước khi chạy (mock dùng để mô phỏng)."""
        return None

    def update_rule_config(self, rule: RuleDef) -> None:
        """Đẩy config rule/camera lên hệ thống trước khi phân tích.

        Mặc định no-op (vd hand-washing không cần). Generic API override để gọi /api/rule.
        """
        return None

    @abstractmethod
    def check_missing(self, videos: list[VideoRef]) -> list[VideoRef]:
        """Trả về danh sách video CHƯA có trên hệ thống (cần upload)."""
        raise NotImplementedError

    @abstractmethod
    def upload(self, video: VideoRef, file_path: str | None = None) -> None:
        """Upload một video lên hệ thống bên thứ 3.

        `file_path`: đường dẫn file local đã tải về (vd từ S3). Mock có thể bỏ qua.
        """
        raise NotImplementedError

    @abstractmethod
    def trigger(self, videos: list[VideoRef], rule: RuleDef) -> str:
        """Kích hoạt phân tích cho tập video. Trả về job_id/batch_code để theo dõi."""
        raise NotImplementedError

    @abstractmethod
    def poll_results(self, job_id: str, videos: list[VideoRef]) -> dict[str, Prediction]:
        """Chờ tới khi xong và trả về {video_id: Prediction}."""
        raise NotImplementedError
