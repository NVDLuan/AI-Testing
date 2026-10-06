"""Xuất report (console/csv + schema QC JSON + dashboard HTML). Luôn chạy, kể cả khi abort."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from ..context import PipelineContext
from .base import Handler

# report/dashboard_template.html (thư mục gốc dự án = pipeline/handlers/../..)
DASHBOARD_TEMPLATE = Path(__file__).resolve().parents[2] / "report" / "dashboard_template.html"


class ReportHandler(Handler):
    """Xuất báo cáo (console/json/csv/html). Luôn chạy — kể cả khi pipeline bị dừng."""

    run_on_abort = True

    def process(self, ctx: PipelineContext) -> None:
        # import cục bộ để tránh phụ thuộc vòng khi chỉ dùng pipeline ở nơi khác.
        from report.reporter import write_reports

        aborted = ctx.aborted or not ctx.reports
        # 'json'/'template' -> file test_results_*.json (mẫu QC) khi chạy XONG.
        # 'html' LUÔN dùng dashboard dựng từ schema QC này (không còn phụ thuộc phải
        # kèm 'json'/'template' trong --formats) -> đây là đường HTML chuẩn duy nhất
        # cho lượt chạy thành công; đường HTML bảng đơn giản cũ chỉ còn dùng khi ABORT
        # (không có comparison để dựng dashboard). Khi abort thì không có kết quả ->
        # 'json' rơi về *_ABORTED.json của write_reports.
        emit_template = (not aborted) and (
                "template" in ctx.report_formats
                or "json" in ctx.report_formats
                or "html" in ctx.report_formats
        )
        emit_dashboard = emit_template and ("html" in ctx.report_formats)
        passthrough = [f for f in ctx.report_formats if f != "template"]
        if emit_template:
            passthrough = [f for f in passthrough if f != "json"]
        if emit_dashboard:
            passthrough = [f for f in passthrough if f != "html"]

        # Tách report theo tenant/version -> test nhiều tenant song song không lẫn
        # file vào nhau: reports/<tenant>/<version>/<file>.
        tenant_slug = ctx.tenant.load_dir or ctx.tenant.name
        version = ctx.tenant.config or ctx.tenant.version or "unversioned"
        output_dir = os.path.join(ctx.report_dir, tenant_slug, version)

        title = f"{ctx.tenant.name} — job {ctx.job.name} (v{ctx.tenant.version})"
        written = write_reports(
            title=title,
            customer_id=tenant_slug,
            reports=ctx.reports,
            output_dir=output_dir,
            formats=passthrough,
            status="aborted" if ctx.aborted else "ok",
            reason=ctx.abort_reason,
            health=ctx.health,
        )
        for fmt, path in written.items():
            if path:
                ctx.log.info("  report[%s] -> %s", fmt, path)

        if emit_template:
            from report.template import write_evidence_raw, write_template

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = write_template(ctx, output_dir=output_dir, stamp=stamp)
            ctx.log.info("  report[template] -> %s", json_path)
            raw_path = write_evidence_raw(ctx, output_dir=output_dir, stamp=stamp)
            if raw_path:
                ctx.log.info("  report[evidence_raw] -> %s", raw_path)

            # Gen dashboard HTML từ file JSON vừa ghi (report/generate_report.py).
            if emit_dashboard:
                from report.generate_report import generate_report as _gen_dashboard

                tpl = str(DASHBOARD_TEMPLATE)
                html_path = json_path[:-5] + ".html" if json_path.endswith(".json") else json_path + ".html"
                try:
                    if _gen_dashboard(json_path, html_path, tpl):
                        ctx.log.info("  report[html] -> %s", html_path)
                except Exception as exc:  # noqa: BLE001 — không để lỗi gen HTML làm hỏng run
                    ctx.log.warning("  gen dashboard HTML lỗi: %s", exc)
