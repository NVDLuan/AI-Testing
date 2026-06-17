"""Data models dùng chung cho cả pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoRef:
    """Tham chiếu tới một video cần phân tích.

    video_id : khoá nội bộ, unique TOÀN CỤC (rule + name) để không đụng giữa các rule.
    name     : tên video gửi cho API (video_name, unique TRONG rule).
    """
    video_id: str
    url: str
    rule_id: str = ""
    camera_id: str = ""
    name: str = ""

    @staticmethod
    def derive_id(url: str) -> str:
        """Suy ra tên từ URL (phần cuối, bỏ query)."""
        tail = url.split("?")[0].rstrip("/").split("/")[-1]
        return tail or url


@dataclass
class GroundTruthRecord:
    """Một dòng ground truth = một video.

    Tenant binary (linfox): chỉ dùng `expected_violation`; `expected_triggered=None`.
    Tenant 3-trạng-thái (bv_c): dùng cả hai — `expected_triggered` (hệ thống CÓ nên
    trigger event không) và `expected_violation` (event đó có vi phạm không).
        No Trigger             -> expected_triggered=False, expected_violation=False
        Trigger (No Violation) -> expected_triggered=True,  expected_violation=False
        Trigger (Violation)    -> expected_triggered=True,  expected_violation=True
    """
    video_id: str               # khoá nội bộ unique toàn cục
    video_url: str              # path_video (nguồn S3)
    expected_violation: bool
    rule_id: str = ""
    camera_id: str = ""
    video_name: str = ""        # tên gửi API (unique trong rule)
    expected_triggered: bool | None = None  # None = tenant binary; có giá trị = 3-state
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Prediction:
    """Kết quả phân tích của bên thứ 3 cho một video.

    `triggered`: hệ thống CÓ sinh event cho video này không (chỉ tenant 3-state dùng;
    None = tenant binary, suy nghĩa trigger không áp dụng).
    """
    video_id: str
    has_violation: bool
    confidence: float | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    triggered: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)
