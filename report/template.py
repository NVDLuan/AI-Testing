"""Xuất report theo template `test_results_*.json` (giống bản mong muốn của QC).

Cấu trúc:
    { version_run, summary{rule_code: status}, details{batch_code: {...}} }
Mỗi (rule, layer) = 1 batch = 1 entry trong `details`, kèm `test_case_validation_result`
(mỗi video một dòng đúng/sai). Dựng từ `PipelineContext` (cần ctx vì có batch_code,
timing, camera config, raw sheet... mà ValidationReport không giữ).

`ctx.reports` có thể có NHIỀU strategy (chain, xem `tenants/factory.py`) — mỗi strategy
1 report riêng. Khi 1 rule có >1 layer, mỗi layer ra 1 batch RIÊNG, đặt tên
`"<rule.code> - <strategy.name>"` (vd "Hygiene - Trigger", "Hygiene - Violation") thay vì
gộp chung — dashboard không cần sửa vì đã render generic theo từng entry `details{}`.
Rule chỉ có 1 layer (mặc định linfox/EWS) giữ nguyên tên `rule.code`, không suffix.

Note của từng dòng test case (expect/actual/validation) khác nhau theo flow — phần đó
KHÔNG nằm ở đây nữa mà tách ra `report/notes_linfox.py` (mặc định, linfox/EWS/karter)
và `report/notes_handwashing.py` (riêng bv_c, đọc theo layer Sequence/Count/Violation &
cond). File này chỉ còn lo phần khung schema CHUNG (batch/rule/video/timing...), gọi
đúng module note theo `is_handwashing(gt)`.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from report.notes_handwashing import handwashing_notes, is_handwashing
from report.notes_linfox import default_notes

if TYPE_CHECKING:
    from pipeline.context import PipelineContext
    from validator.validator import ValidationReport


def _raw_get(raw: dict, *keys: str) -> str:
    """Lấy giá trị cột sheet theo nhiều tên khả dĩ (sheet mỗi tenant đặt tên khác nhau)."""
    for k in keys:
        if k in raw and str(raw[k]).strip():
            return str(raw[k]).strip()
    return ""


def image_config_url(url_raw: str, version_config: str) -> str:
    """Suy URL ảnh debug zone từ URL video raw (cùng thư mục, thêm debug_zone_<vc>).

    Nguồn chân lý DUY NHẤT cho quy ước đặt tên này — `DrawZoneHandler`
    (pipeline/handlers.py) cũng gọi hàm này để biết vẽ/upload ảnh vào đúng chỗ mà
    report sẽ trỏ tới, tránh lệch giữa URL hiển thị và object thật trên S3.
    """
    if not url_raw or "/" not in url_raw:
        return ""
    prefix, fname = url_raw.rsplit("/", 1)
    base = fname.rsplit(".", 1)[0]
    return f"{prefix}/debug_zone_{version_config}/{base}_zones.webp"


def _fmt_duration(sec: float) -> str:
    s = int(sec)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60}s"


def _strip_ext(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def _testcase_row(gt, comp, pred, version_config: str, strategy: str = "") -> dict[str, Any]:
    outcome = comp.outcome if comp else "MISSING"
    raw = gt.raw or {}
    test_case_description = _raw_get(raw, "Test Case Description", "Short description")
    if is_handwashing(gt):
        # bv_c: QC note = so sánh expected/actual rồi diễn giải lý do sai (giữ nguyên).
        expect_note, actual_note, validation_note = handwashing_notes(gt, comp, pred, strategy, outcome)
    else:
        # linfox/EWS/karter: QC note = Test Case Description khai trong sheet, không
        # phải câu diễn giải outcome (khác bv_c) — expect/actual vẫn theo outcome.
        expect_note, actual_note, _ = default_notes(outcome)
        validation_note = test_case_description
    url_raw = _raw_get(raw, "Video Raw", "Video Name1") or gt.video_url
    # evidence đã publish lên S3 (PublishEvidenceHandler gắn vào comp.detail)
    fb_urls = list((comp.detail or {}).get("evidence_urls", [])) if comp and comp.detail else []
    events = list(pred.events) if pred and pred.events else []
    # Số evidence hệ thống trả về cho video này (mỗi event = 1 evidence/annotated video).
    evidence_count = len(events)
    # 1 evidence -> trường đơn `url_video_evidence`; nhiều -> trường `list_...`
    # (giống validator chọn cách biểu diễn theo dữ liệu). Bên nào không dùng để rỗng.
    multi = evidence_count > 1
    single_url = fb_urls[0] if (not multi and fb_urls) else ""
    list_urls = fb_urls if multi else []

    return {
        "video_name": _raw_get(raw, "Video Name") or gt.video_name,
        # 1 video có thể sinh nhiều event -> nhiều dòng test case; event_index/event_count
        # cho biết dòng này là event thứ mấy trong tổng số event của video (0/1 nếu 1 outcome).
        "event_index": comp.event_index if comp else 0,
        "event_count": comp.event_count if comp else 1,
        "test_case_id": _raw_get(raw, "TC", "Test Case", "test_case_id"),
        "test_case_description": test_case_description,
        "expected_status": _raw_get(raw, "Expected Status") or ("Approve" if gt.expected_violation else "Reject"),
        "expect_note": expect_note,
        "actual_note": actual_note,
        "total_frames": 0,
        "matched_frames": 0,
        "accuracy": 0.0,
        "detect_result": outcome,
        "validation_note": validation_note,
        "url_video_raw": url_raw,
        "url_image_config": image_config_url(url_raw, version_config),
        "evidence_count": evidence_count,
        "url_video_evidence": single_url,
        "url_video_evidence_filebrowser": single_url,
        "list_url_video_evidence": list_urls,
        "list_url_video_evidence_filebrowser": list_urls,
        "url_video_expected": "",
        "url_video_old_evidence": "",
        "json_video_evidence": events,
        "frame_results": {"expected": []},
    }


def _rule_detail(
        ctx: "PipelineContext", rule, stamp: str, status: str,
        report: "ValidationReport", label: str,
) -> dict[str, Any]:
    tenant = ctx.tenant
    version_config = tenant.config or tenant.version
    videos = ctx.videos_by_rule.get(rule.code, [])
    gt_by_id = {g.video_id: g for g in ctx.ground_truth if g.rule_id == rule.name}
    # 1 video có thể sinh > 1 Comparison (1 outcome/event) -> gom theo list, không ghi đè.
    comps_by_id: dict[str, list] = {}
    for c in report.comparisons:
        comps_by_id.setdefault(c.video_id, []).append(c)
    strategy = report.strategy

    rows: list[dict] = []
    # passed/failed/total đếm theo VIDEO (không theo event) -> khớp nhãn "TOTAL VIDEO TEST"
    # trên dashboard; 1 video coi là "passed" chỉ khi MỌI outcome (mọi event) của nó đều
    # TP/TN, có ít nhất 1 FP/FN/MISSING thì video đó "failed" (dù có event khác đạt).
    passed = failed = total = total_evidence = 0
    for v in videos:
        gt = gt_by_id.get(v.video_id)
        if gt is None:
            continue
        comps = comps_by_id.get(v.video_id)
        if comps is None:
            # Layer này KHÔNG chấm video này (vd "Hygiene - Violation" khi GT không cần
            # trigger — TriggerStrategy đã tính lỗi trigger ở layer riêng) -> bỏ qua hẳn,
            # không tính vào total/passed/failed, KHÔNG hiển thị nhầm thành MISSING (đó là
            # khi thật sự không có prediction, validate() vẫn tạo Comparison ở MỌI layer).
            continue
        pred = ctx.predictions.get(v.video_id)
        # evidence đếm 1 lần/video (không nhân theo số dòng event) để khỏi trùng lặp.
        total_evidence += len(pred.events) if pred and pred.events else 0
        video_passed = True
        for comp in comps:
            row = _testcase_row(gt, comp, pred, version_config, strategy)
            rows.append(row)
            if row["detect_result"] not in ("TP", "TN"):
                video_passed = False
        total += 1
        if video_passed:
            passed += 1
        else:
            failed += 1
    config = {cam_id: cam.raw for cam_id, cam in rule.cameras.items()}
    api_resp = ctx.rule_api_response.get(rule.code) or {"rule_code": rule.code, "config": config}
    uploaded = sum(1 for m in ctx.missing if m.rule_id == rule.name)

    videos_config: dict[str, list[str]] = {}
    videos_metadata: list[dict] = []
    for v in videos:
        gt = gt_by_id.get(v.video_id)
        raw = gt.raw if gt else {}
        videos_config.setdefault(v.camera_id or "unknown", []).append(_strip_ext(v.name))
        videos_metadata.append({
            "video_name": (gt.video_name if gt else v.name),
            "camera_code": v.camera_id,
            "camera_name": _raw_get(raw, "Camera Name", "camera_name"),
        })

    now = time.time()
    # batch_code phải UNIQUE giữa các layer của cùng 1 rule thật (key trong details{}) ->
    # thêm suffix layer khi label khác rule.code (chain >1 phần tử); rule đơn layer giữ
    # nguyên batch_code như trước (không đổi output cho linfox/EWS).
    real_batch_code = ctx.batch_codes.get(rule.code, f"{rule.code}_{stamp}")
    batch_code = real_batch_code if label == rule.code else f"{real_batch_code} - {label.split(' - ', 1)[1]}"
    return {
        "batch_code": batch_code,
        "timestamp": stamp,
        "tenant": tenant.name,
        "rule_name": label,
        "rule_code": label,
        "rule_type": rule.type,
        "tenant_dir": tenant.dir or tenant.load_dir,
        "total_testcases": total,
        "uploaded_videos": uploaded,
        "videos_config": videos_config,
        "rule_config": {
            "rule_code": rule.code,
            "config": config,
            "api_response": api_resp,
            "camera_count": len(config),
            "camera_codes": list(config.keys()),
        },
        "videos_metadata": videos_metadata,
        "test_statistics": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / total * 100, 2) if total else 0.0,
            "total_evidence": total_evidence,
        },
        "timing": {
            "start_time": int(ctx.started_at),
            "end_time": int(now),
            "duration_seconds": round(now - ctx.started_at, 2) if ctx.started_at else 0.0,
            "duration_formatted": _fmt_duration(now - ctx.started_at) if ctx.started_at else "0s",
        },
        "timedate": stamp.split("_")[0],
        "test_case_validation_result": rows,
        "status": status,
        "expected_events": {},
    }


def _health_block(ctx: "PipelineContext") -> dict[str, Any]:
    """Khối health top-level: raw (phản hồi /api/get-overall-health-status) + summary."""
    h = ctx.health
    if h is None:
        return {}
    raw = (h.detail or {}).get("system", {}) or {}
    avail = raw.get("disk_available_bytes")
    evaluated = {
        "cpu_usage_percentage": raw.get("cpu_usage_percentage"),
        "ram_usage_percentage": raw.get("ram_usage_percentage"),
        "gpu_usage_percentage": raw.get("gpu_usage_percentage"),
        "disk_available_bytes": avail,
        "disk_available_gb": round(avail / (1024 ** 3), 2) if isinstance(avail, (int, float)) else None,
        "deepstream_is_healthy": raw.get("deepstream_is_healthy"),
    }
    return {
        "raw": raw,
        "summary": {
            "is_healthy": h.healthy,
            "reasons": list((h.detail or {}).get("over_threshold", [])),
            "evaluated": evaluated,
        },
    }


def build_template(ctx: "PipelineContext", stamp: str) -> dict[str, Any]:
    tenant = ctx.tenant
    status = "aborted" if ctx.aborted else "success"
    summary: dict[str, str] = {}
    details: dict[str, dict] = {}
    multi_layer = len(ctx.reports) > 1
    for rule in ctx.rules:
        if not ctx.videos_by_rule.get(rule.code):
            continue
        for name, report in ctx.reports.items():
            label = f"{rule.code} - {name}" if multi_layer else rule.code
            detail = _rule_detail(ctx, rule, stamp, status, report, label)
            details[detail["batch_code"]] = detail
            summary[label] = detail["status"]

    return {
        "version_run": {
            "job": ctx.job.name,
            "version_ai": tenant.version,
            "version_config": tenant.config or tenant.version,
        },
        "summary": summary,
        "details": details,
        "health": _health_block(ctx),
    }


def write_template(ctx: "PipelineContext", output_dir: str = "reports", stamp: str = "") -> str:
    """Ghi file test_results_<stamp>.json theo template. Trả về đường dẫn."""
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"test_results_{stamp}.json")
    data = build_template(ctx, stamp)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
