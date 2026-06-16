"""Xuất báo cáo: console + JSON + CSV."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

from interfaces.analysis_client import HealthStatus
from validator.metrics import ConfusionMatrix
from validator.validator import ValidationReport


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _matrix_line(name: str, cm: ConfusionMatrix) -> str:
    return (
        f"{name:<22} "
        f"N={cm.total:<4} TP={cm.tp:<3} FP={cm.fp:<3} FN={cm.fn:<3} TN={cm.tn:<3} "
        f"Recall={_fmt_pct(cm.recall):<8} FAR={_fmt_pct(cm.far):<8} "
        f"Prec={_fmt_pct(cm.precision):<8} F1={_fmt_pct(cm.f1)}"
    )


def render_console(title: str, report: ValidationReport) -> str:
    lines: list[str] = []
    lines.append("=" * 104)
    lines.append(f"BÁO CÁO TEST — {title}")
    lines.append("=" * 104)
    lines.append(_matrix_line("TỔNG (overall)", report.overall))

    if report.by_rule:
        lines.append("-" * 104)
        lines.append("Theo RULE:")
        for rid, cm in sorted(report.by_rule.items()):
            lines.append("  " + _matrix_line(rid, cm))

    if report.by_camera:
        lines.append("-" * 104)
        lines.append("Theo CAMERA:")
        for cid, cm in sorted(report.by_camera.items()):
            lines.append("  " + _matrix_line(cid, cm))

    errors = [c for c in report.comparisons if c.outcome in ("FP", "FN")]
    if errors:
        lines.append("-" * 104)
        lines.append(f"CA SAI ({len(errors)}):")
        for c in errors:
            lines.append(
                f"  [{c.outcome}] {c.video_id} (rule={c.rule_id}, cam={c.camera_id}) "
                f"expected={c.expected} predicted={c.predicted} conf={c.confidence}"
            )

    if report.missing:
        lines.append("-" * 104)
        lines.append(f"THIẾU KẾT QUẢ ({len(report.missing)}):")
        for c in report.missing:
            lines.append(f"  {c.video_id} ({c.video_url})")

    lines.append("=" * 104)
    return "\n".join(lines)


def render_abort_console(title: str, reason: str, health: HealthStatus | None) -> str:
    lines = ["=" * 104, f"BÁO CÁO TEST — {title}   [DỪNG / ABORTED]", "=" * 104]
    lines.append(f"Lý do: {reason or 'không rõ'}")
    if health is not None:
        sysd = health.detail.get("system", {})
        if sysd:
            lines.append(
                "Health: deepstream={} cpu={:.0f}% gpu={:.0f}% ram={:.0f}% disk={:.0f}%".format(
                    sysd.get("deepstream_is_healthy"),
                    sysd.get("cpu_usage_percentage", 0),
                    sysd.get("gpu_usage_percentage", 0),
                    sysd.get("ram_usage_percentage", 0),
                    sysd.get("disk_usage_percentage", 0),
                )
            )
        if "system_error" in health.detail:
            lines.append(f"Health error: {health.detail['system_error']}")
    lines.append("Không chạy test (pipeline dừng ở bước health check).")
    lines.append("=" * 104)
    return "\n".join(lines)


def _abort_dict(title: str, reason: str, health: HealthStatus | None) -> dict:
    return {
        "title": title,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "aborted",
        "reason": reason,
        "health": health.detail if health else {},
    }


def _to_dict(title: str, report: ValidationReport) -> dict:
    return {
        "title": title,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall": report.overall.as_dict(),
        "by_rule": {k: v.as_dict() for k, v in report.by_rule.items()},
        "by_camera": {k: v.as_dict() for k, v in report.by_camera.items()},
        "comparisons": [
            {
                "video_id": c.video_id,
                "video_url": c.video_url,
                "rule_id": c.rule_id,
                "camera_id": c.camera_id,
                "expected": c.expected,
                "predicted": c.predicted,
                "outcome": c.outcome,
                "confidence": c.confidence,
            }
            for c in report.comparisons
        ],
    }


def write_reports(
    title: str,
    customer_id: str,
    report: ValidationReport | None,
    output_dir: str = "reports",
    formats: list[str] | None = None,
    status: str = "ok",
    reason: str = "",
    health: HealthStatus | None = None,
) -> dict[str, str]:
    """Sinh report theo format yêu cầu. Trả về {format: path|''}.

    status != "ok" (hoặc report None) -> ghi report DỪNG (health fail), bỏ CSV.
    """
    formats = formats or ["console"]
    written: dict[str, str] = {}
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    aborted = status != "ok" or report is None
    base = f"{customer_id}_{stamp}{'_ABORTED' if aborted else ''}"

    if "console" in formats:
        text = render_abort_console(title, reason, health) if aborted else render_console(title, report)
        print(text)
        written["console"] = ""

    if "json" in formats:
        path = os.path.join(output_dir, f"{base}.json")
        data = _abort_dict(title, reason, health) if aborted else _to_dict(title, report)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        written["json"] = path

    if "csv" in formats and not aborted:
        path = os.path.join(output_dir, f"{base}.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["video_id", "video_url", "rule_id", "camera_id",
                 "expected", "predicted", "outcome", "confidence"]
            )
            for c in report.comparisons:
                w.writerow(
                    [c.video_id, c.video_url, c.rule_id, c.camera_id,
                     c.expected, c.predicted, c.outcome, c.confidence]
                )
        written["csv"] = path

    return written
