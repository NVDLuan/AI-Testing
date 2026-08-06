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

    def update_rule_config(self, rule: RuleDef, videos: list[VideoRef]) -> None:
        """Đẩy config rule/camera lên hệ thống trước khi phân tích.

        `videos`: danh sách video của rule này (cùng tập được LoadGroundTruthHandler
        dựng) — generic API dùng để sinh camera_code ẢO riêng cho từng video, xem
        `LinfoxHttpClient._video_camera_code`. Mặc định no-op (vd hand-washing không cần).
        """
        return None

    @abstractmethod
    def check_missing(self, videos: list[VideoRef]) -> list[VideoRef]:
        """Trả về danh sách video CHƯA có trên hệ thống (cần upload)."""
        raise NotImplementedError

    @abstractmethod
    def upload(self, video: VideoRef, content: bytes | None = None, filename: str = "") -> None:
        """Upload một video lên hệ thống bên thứ 3.

        `content`: nội dung video (bytes) lấy từ VideoStore.fetch — upload thẳng, không
        qua đĩa. `filename`: tên file gửi kèm multipart. Mock có thể bỏ qua.
        """
        raise NotImplementedError

    @abstractmethod
    def trigger(self, videos: list[VideoRef], rule: RuleDef, batch_code: str) -> str:
        """Kích hoạt phân tích cho tập video. Trả về job_id/batch_code để theo dõi."""
        raise NotImplementedError

    @abstractmethod
    def poll_results(self, job_id: str, videos: list[VideoRef]) -> dict[str, Prediction]:
        """Chờ tới khi xong và trả về {video_id: Prediction}."""
        raise NotImplementedError
