"""Bước 0: kiểm tra sức khoẻ hệ thống bên thứ 3 trước khi test."""
from __future__ import annotations

from ..context import PipelineContext
from .base import Handler


class HealthCheckHandler(Handler):
    """Kiểm tra sức khoẻ hệ thống bên thứ 3 TRƯỚC khi test. Không khoẻ -> dừng."""

    def process(self, ctx: PipelineContext) -> None:
        status = ctx.client.health_check()
        ctx.health = status
        sys_info = status.detail.get("system", {})
        if sys_info:
            ctx.log.info(
                "  health: deepstream=%s cpu=%.0f%% ram=%.0f%% gpu=%.0f%% disk=%.0f%%",
                sys_info.get("deepstream_is_healthy"),
                sys_info.get("cpu_usage_percentage", 0),
                sys_info.get("ram_usage_percentage", 0),
                sys_info.get("gpu_usage_percentage", 0),
                sys_info.get("disk_usage_percentage", 0),
            )
        if not status.healthy:
            over = status.detail.get("over_threshold")
            reason = "Health check thất bại"
            if over:
                reason = f"Health check thất bại: vượt ngưỡng {', '.join(over)}"
            ctx.abort(reason)  # chi tiết đầy đủ nằm ở ctx.health (in trong report)
        else:
            ctx.log.info("  health: OK")
