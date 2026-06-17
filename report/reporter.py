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
    if report.strategy:
        lines.append(f"(luật chấm: {report.strategy})")
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


def _metrics_row_html(name: str, cm: ConfusionMatrix) -> str:
    return (
        "<tr>"
        f"<td>{_html_escape(name)}</td>"
        f"<td>{cm.total}</td><td>{cm.tp}</td><td>{cm.fp}</td><td>{cm.fn}</td><td>{cm.tn}</td>"
        f"<td>{_fmt_pct(cm.recall)}</td><td>{_fmt_pct(cm.far)}</td>"
        f"<td>{_fmt_pct(cm.precision)}</td><td>{_fmt_pct(cm.f1)}</td>"
        "</tr>"
    )


def _metrics_table_html(caption: str, rows: list[tuple[str, ConfusionMatrix]]) -> str:
    head = (
        "<tr><th>Nhóm</th><th>N</th><th>TP</th><th>FP</th><th>FN</th><th>TN</th>"
        "<th>Recall</th><th>FAR</th><th>Prec</th><th>F1</th></tr>"
    )
    body = "".join(_metrics_row_html(n, cm) for n, cm in rows)
    return f"<h3>{_html_escape(caption)}</h3><table>{head}{body}</table>"


def _evidence_links_html(detail: dict | None) -> str:
    urls = (detail or {}).get("evidence_urls") if detail else None
    if not urls:
        return ""
    items = "".join(
        f'<li><a href="{_html_escape(u)}" target="_blank" rel="noopener">evidence {i + 1}</a></li>'
        for i, u in enumerate(urls)
    )
    return f"<ul class='ev'>{items}</ul>"


def render_html(title: str, report: ValidationReport) -> str:
    rows_overall = [("TỔNG", report.overall)]
    rules = sorted(report.by_rule.items())
    cams = sorted(report.by_camera.items())

    comp_head = (
        "<tr><th>Video</th><th>Rule</th><th>Camera</th><th>Expected</th>"
        "<th>Predicted</th><th>Outcome</th><th>Evidence</th></tr>"
    )
    comp_rows = []
    for c in report.comparisons:
        cls = "" if c.outcome in ("TP", "TN") else (" class='bad'" if c.outcome != "MISSING" else " class='miss'")
        comp_rows.append(
            f"<tr{cls}>"
            f"<td>{_html_escape(c.video_id)}</td>"
            f"<td>{_html_escape(c.rule_id)}</td>"
            f"<td>{_html_escape(c.camera_id)}</td>"
            f"<td>{_html_escape(c.expected)}</td>"
            f"<td>{_html_escape(c.predicted)}</td>"
            f"<td>{_html_escape(c.outcome)}</td>"
            f"<td>{_evidence_links_html(c.detail)}</td>"
            "</tr>"
        )
    strat = f"<p class='sub'>Luật chấm: <b>{_html_escape(report.strategy)}</b></p>" if report.strategy else ""

    return f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<title>{_html_escape(title)}</title>
<style>
 body{{font-family:system-ui,Arial,sans-serif;margin:24px;color:#1a1a1a}}
 h1{{font-size:20px}} h3{{margin-top:24px}}
 .sub{{color:#666;margin-top:-8px}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}}
 th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left}}
 th{{background:#f4f4f4}}
 tr.bad td{{background:#fdecea}} tr.miss td{{background:#fff6e5}}
 ul.ev{{margin:0;padding-left:16px}} a{{color:#0a58ca}}
</style></head><body>
<h1>BÁO CÁO TEST — {_html_escape(title)}</h1>
{strat}
{_metrics_table_html("Tổng quan", rows_overall)}
{_metrics_table_html("Theo Rule", rules) if rules else ""}
{_metrics_table_html("Theo Camera", cams) if cams else ""}
<h3>Chi tiết theo video ({len(report.comparisons)})</h3>
<table>{comp_head}{"".join(comp_rows)}</table>
</body></html>"""


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


def _to_dict(title: str, report: ValidationReport) -> dict:
    return {
        "title": title,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": report.strategy,
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
                "detail": c.detail,
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

    if "html" in formats:
        path = os.path.join(output_dir, f"{base}.html")
        html = render_abort_html(title, reason, health) if aborted else render_html(title, report)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        written["html"] = path

    return written
