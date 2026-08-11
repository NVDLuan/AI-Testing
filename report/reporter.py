"""Xuất báo cáo: console + CSV luôn; JSON + HTML chỉ cho trường hợp ABORT.

Nhận `reports: dict[str, ValidationReport]` (khoá = tên strategy/layer, xem
`pipeline.context.PipelineContext.reports`) thay vì 1 report duy nhất — tenant không
dùng chain (1 phần tử, khoá bất kỳ) ra output y hệt trước đây; tenant có chain (bv_c,
>1 phần tử) thì mỗi layer thêm 1 mục/section riêng.

JSON/HTML của lượt chạy THÀNH CÔNG không sinh ở đây — luôn là schema QC chuẩn duy nhất
`test_results_*.json` + dashboard (`report.template` + `report/dashboard_template.html`),
điều phối bởi `pipeline.handlers.ReportHandler` (`emit_template`/`emit_dashboard`).
`write_reports` chỉ còn render JSON/HTML rút gọn cho trường hợp ABORT (không có
comparison để dựng schema QC đầy đủ).
"""
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


def _render_report_console(report: ValidationReport) -> list[str]:
    """Các dòng ma trận/lỗi/thiếu — KHÔNG gồm dòng '(luật chấm: ...)' (caller tự thêm)."""
    lines: list[str] = [_matrix_line("TỔNG (overall)", report.overall)]

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

    # "Ca sai" = mọi outcome không phải đúng (TP/TN) và không phải thiếu kết quả.
    errors = [c for c in report.comparisons if c.outcome not in ("TP", "TN", "MISSING")]
    if errors:
        lines.append("-" * 104)
        lines.append(f"CA SAI ({len(errors)}):")
        for c in errors:
            extra = f" detail={c.detail}" if c.detail else ""
            lines.append(
                f"  [{c.outcome}] {c.video_id} (rule={c.rule_id}, cam={c.camera_id}) "
                f"expected={c.expected} predicted={c.predicted} conf={c.confidence}{extra}"
            )

    if report.missing:
        lines.append("-" * 104)
        lines.append(f"THIẾU KẾT QUẢ ({len(report.missing)}):")
        for c in report.missing:
            lines.append(f"  {c.video_id} ({c.video_url})")

    return lines


def render_console(title: str, reports: dict[str, ValidationReport]) -> str:
    lines: list[str] = ["=" * 104, f"BÁO CÁO TEST — {title}"]

    if len(reports) == 1:
        (report,) = reports.values()
        if report.strategy:
            lines.append(f"(luật chấm: {report.strategy})")
        lines.append("=" * 104)
        lines.extend(_render_report_console(report))
        lines.append("=" * 104)
        return "\n".join(lines)

    lines.append("=" * 104)
    for name, report in reports.items():
        lines.append("-" * 104)
        header = f"LAYER: {name}"
        if report.strategy:
            header += f"  (luật chấm: {report.strategy})"
        lines.append(header)
        lines.extend(_render_report_console(report))
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


def _html_escape(text: object) -> str:
    s = str(text)
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_abort_html(title: str, reason: str, health: HealthStatus | None) -> str:
    sysd = (health.detail.get("system", {}) if health else {}) or {}
    health_line = ""
    if sysd:
        health_line = (
            "<p>Health: deepstream={} cpu={:.0f}% gpu={:.0f}% ram={:.0f}% disk={:.0f}%</p>".format(
                sysd.get("deepstream_is_healthy"),
                sysd.get("cpu_usage_percentage", 0),
                sysd.get("gpu_usage_percentage", 0),
                sysd.get("ram_usage_percentage", 0),
                sysd.get("disk_usage_percentage", 0),
            )
        )
    return f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8"><title>{_html_escape(title)}</title></head>
<body style="font-family:system-ui,Arial,sans-serif;margin:24px">
<h1>BÁO CÁO TEST — {_html_escape(title)} [DỪNG]</h1>
<p>Lý do: {_html_escape(reason or 'không rõ')}</p>
{health_line}
<p>Không chạy test (pipeline dừng ở bước health check).</p>
</body></html>"""


def _abort_dict(title: str, reason: str, health: HealthStatus | None) -> dict:
    return {
        "title": title,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "aborted",
        "reason": reason,
        "health": health.detail if health else {},
    }


def write_reports(
    title: str,
    customer_id: str,
    reports: dict[str, ValidationReport] | None,
    output_dir: str = "reports",
    formats: list[str] | None = None,
    status: str = "ok",
    reason: str = "",
    health: HealthStatus | None = None,
) -> dict[str, str]:
    """Sinh report theo format yêu cầu. Trả về {format: path|''}.

    status != "ok" (hoặc reports rỗng) -> ghi report DỪNG (health fail), bỏ CSV.
    'json' và 'html' ở đây CHỈ còn xử lý trường hợp abort (`_abort_dict` /
    `render_abort_html`) — JSON/HTML của lượt chạy thành công luôn là schema QC
    `test_results_*.json` dựng từ `report.template` (xem `pipeline.handlers.ReportHandler`
    và `emit_template`), không đi qua hàm này nữa.
    """
    formats = formats or ["console"]
    written: dict[str, str] = {}
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    aborted = status != "ok" or not reports
    base = f"{customer_id}_{stamp}{'_ABORTED' if aborted else ''}"

    if "console" in formats:
        text = render_abort_console(title, reason, health) if aborted else render_console(title, reports)
        print(text)
        written["console"] = ""

    if "json" in formats and aborted:
        path = os.path.join(output_dir, f"{base}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_abort_dict(title, reason, health), f, ensure_ascii=False, indent=2)
        written["json"] = path

    if "csv" in formats and not aborted:
        path = os.path.join(output_dir, f"{base}.csv")
        multi = len(reports) > 1
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["video_id", "video_url", "layer", "rule_id", "camera_id",
                 "expected", "predicted", "outcome", "confidence"]
            )
            for name, report in reports.items():
                layer = name if multi else ""
                for c in report.comparisons:
                    w.writerow(
                        [c.video_id, c.video_url, layer, c.rule_id, c.camera_id,
                         c.expected, c.predicted, c.outcome, c.confidence]
                    )
        written["csv"] = path

    if "html" in formats and aborted:
        path = os.path.join(output_dir, f"{base}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_abort_html(title, reason, health))
        written["html"] = path

    return written
