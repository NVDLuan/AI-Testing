"""Xuất report theo template `test_results_*.json` (giống bản mong muốn của QC).

Cấu trúc:
    { version_run, summary{rule_code: status}, details{batch_code: {...}} }
Mỗi rule = 1 batch = 1 entry trong `details`, kèm `test_case_validation_result`
(mỗi video một dòng đúng/sai). Dựng từ `PipelineContext` (cần ctx vì có batch_code,
timing, camera config, raw sheet... mà ValidationReport không giữ).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pipeline.context import PipelineContext

# outcome -> (expect_note, actual_note, validation_note, detect_result)
_NOTE_BY_OUTCOME: dict[str, tuple[str, str, str, str]] = {
    "TP": ("Phát hiện được vi phạm", "Phát hiện được vi phạm", "", "PASSED"),
    "TN": ("Không có vi phạm", "Không có vi phạm", "", "PASSED"),
    "FN": ("Phát hiện được vi phạm", "Bỏ sót vi phạm: ",
           "Missed event", "FAILED"),
    "FP": ("Không có vi phạm", "Báo nhầm vi phạm: ",
           "Actual result differs from the expected result", "FAILED"),
    "MISSING": ("", "Không có kết quả phân tích",
                "No analysis result returned for this video", "FAILED"),
}


def _seq_str(seq) -> str:
    return ",".join(str(x) for x in (seq or []))


def _is_handwashing(gt) -> bool:
    """Nhận diện case bv_c V2: có Step Sequence hoặc nhãn 3-state (expected_triggered)."""
    return gt.expected_triggered is not None or "Step Sequence" in (gt.raw or {})


def _handwashing_notes(gt, pred) -> tuple[str, str]:
    """Note theo V2: 'Violation: <T/F> - sequence step: <a,b,c>' cho expected & actual."""
    exp_seq = [int(x) for x in re.findall(r"\d+", str((gt.raw or {}).get("Step Sequence", "")))]
    expect_note = f"Sequence step: {_seq_str(exp_seq)}"

    if pred and pred.events:
        act_viol = bool(pred.has_violation)
        seqs = [ev.get("sequence", []) for ev in pred.events if ev.get("sequence")]
        act_seq = " | ".join(_seq_str(s) for s in seqs)  # nhiều event -> ngăn bằng ' | '
    else:
        act_viol, act_seq = False, ""
    actual_note = f"Sequence step: {act_seq}"
    return expect_note, actual_note


def _b_counts(raw: dict) -> dict[str, int]:
    """Đọc count kỳ vọng từ các cột B0..B6 trong sheet -> {code: int}."""
    out: dict[str, int] = {}
    for k, v in (raw or {}).items():
        if re.match(r"^B\d+$", str(k).strip(), re.I):
            try:
                out[str(k).strip().upper()] = int(float(str(v).strip()))
            except (ValueError, TypeError):
                out[str(k).strip().upper()] = 0
    return out


def _counts_kv(counts: dict) -> str:
    return ",".join(f"{k}:{counts[k]}" for k in sorted(counts))


def _handwashing_count_notes(gt, pred) -> tuple[str, str]:
    """Note theo V3: 'Violation: <T/F> - step count: B1:5,B2:3,...' cho expected & actual."""
    exp = _b_counts(gt.raw)
    expect_note = f"Violation: {bool(gt.expected_violation)} - step count: {_counts_kv(exp)}"

    if pred and pred.events:
        # gộp count theo từng step qua các event (lấy max), rồi căn theo các code expected.
        merged: dict[str, int] = {}
        for ev in pred.events:
            for code, c in (ev.get("counts") or {}).items():
                cu = str(code).strip().upper()
                try:
                    c = int(float(str(c)))
                except (ValueError, TypeError):
                    c = 0
                merged[cu] = max(merged.get(cu, 0), c)
        shown = {code: merged.get(code, 0) for code in exp} if exp else merged
        actual_note = f"Violation: {bool(pred.has_violation)} - step count: {_counts_kv(shown)}"
    else:
        actual_note = "Violation: False - step count: "
    return expect_note, actual_note


def _raw_get(raw: dict, *keys: str) -> str:
    """Lấy giá trị cột sheet theo nhiều tên khả dĩ (sheet mỗi tenant đặt tên khác nhau)."""
    for k in keys:
        if k in raw and str(raw[k]).strip():
            return str(raw[k]).strip()
    return ""


def _image_config_url(url_raw: str, version_config: str) -> str:
    """Suy URL ảnh debug zone từ URL video raw (cùng thư mục, thêm debug_zone_<vc>)."""
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
    expect_note, actual_note, validation_note, _detect = _NOTE_BY_OUTCOME.get(
        outcome, ("", "", "", "FAILED")
    )
    # bv_c: note = Violation + tiêu chí của luật chấm (V3 -> count từng bước, còn lại ->
    # Step Sequence). detect_result/validation_note vẫn theo outcome (TP/FP/FN/TN).
    if _is_handwashing(gt):
        if strategy in ("hand_washing_v4", "hand_washing_v5"):
            seq_e, seq_a = _handwashing_notes(gt, pred)
            cnt_e, cnt_a = _handwashing_count_notes(gt, pred)
            # gộp: 'Violation + sequence step + step count' (bỏ tiền tố 'Violation' trùng ở count).
            expect_note = f"{seq_e} - step count: {cnt_e.split('step count: ', 1)[-1]}"
            actual_note = f"{seq_a} - step count: {cnt_a.split('step count: ', 1)[-1]}"
        elif strategy == "hand_washing_v3":
            expect_note, actual_note = _handwashing_count_notes(gt, pred)
        else:
            expect_note, actual_note = _handwashing_notes(gt, pred)
        # No Trigger: bỏ qua note step/sequence/count, chỉ hiển thị "Không trigger".
        if gt.expected_triggered is False:
            expect_note = "Không trigger"
        if not (pred and pred.events):
            actual_note = "Không trigger"
    raw = gt.raw or {}
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
        "test_case_id": _raw_get(raw, "TC", "Test Case", "test_case_id"),
        "test_case_description": _raw_get(raw, "Test Case Description", "Short description"),
        "expected_status": _raw_get(raw, "Expected Status") or ("Approve" if gt.expected_violation else "Reject"),
        "expect_note": expect_note,
        "actual_note": actual_note,
        "total_frames": 0,
        "matched_frames": 0,
        "accuracy": 0.0,
        "detect_result": outcome,
        "validation_note": validation_note,
        "url_video_raw": url_raw,
        "url_image_config": _image_config_url(url_raw, version_config),
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


def _rule_detail(ctx: "PipelineContext", rule, stamp: str, status: str) -> dict[str, Any]:
    tenant = ctx.tenant
    version_config = tenant.config or tenant.version
    videos = ctx.videos_by_rule.get(rule.code, [])
    gt_by_id = {g.video_id: g for g in ctx.ground_truth if g.rule_id == rule.name}
    comp_by_id = {c.video_id: c for c in (ctx.report.comparisons if ctx.report else [])}
    strategy = ctx.report.strategy if ctx.report else ""

    rows: list[dict] = []
    passed = failed = total_evidence = 0
    for v in videos:
        gt = gt_by_id.get(v.video_id)
        if gt is None:
            continue
        comp = comp_by_id.get(v.video_id)
        pred = ctx.predictions.get(v.video_id)
        row = _testcase_row(gt, comp, pred, version_config, strategy)
        rows.append(row)
        if row["detect_result"] in ("TP", "TN"):
            passed += 1
        else:
            failed += 1
        total_evidence += len(row["json_video_evidence"])

    total = len(rows)
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
    return {
        "batch_code": ctx.batch_codes.get(rule.code, f"{rule.code}_{stamp}"),
        "timestamp": stamp,
        "tenant": tenant.name,
        "rule_name": rule.name,
        "rule_code": rule.code,
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
    for rule in ctx.rules:
        if not ctx.videos_by_rule.get(rule.code):
            continue
        detail = _rule_detail(ctx, rule, stamp, status)
        details[detail["batch_code"]] = detail
        summary[rule.code] = detail["status"]

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
