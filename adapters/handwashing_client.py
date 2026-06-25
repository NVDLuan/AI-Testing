"""Client cho API hand-washing (/api/hand-washing/*) — dùng cho tenant bv_c.

Quy ước có vi phạm: event.violation == True cho video đó.
"""
from __future__ import annotations

import logging
import os
import time

from configs.schema import RuleDef
from interfaces.analysis_client import AnalysisClient
from validator.models import Prediction, VideoRef

from ._http_base import BaseHttpClient, norm_name

log = logging.getLogger("ai_testing.handwashing")

_BASE = "/api/hand-washing"


class HandWashingHttpClient(BaseHttpClient, AnalysisClient):
    def __init__(self, *args, recordings_dir: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._batch_rule: dict[str, RuleDef] = {}
        # Thư mục chứa file keypoints .txt (sinh kèm video, cùng tên gốc). Code sinh txt
        # ghi vào đây; upload đọc lên gửi field 'keypoints'.
        self._recordings_dir = recordings_dir

    def check_missing(self, videos: list[VideoRef]) -> list[VideoRef]:
        resp = self._session.post(
            self._url(f"{_BASE}/videos/check"),
            json={"video_names": [v.name for v in videos]},
            timeout=30,
        )
        resp.raise_for_status()
        norm_missing = {norm_name(m) for m in resp.json().get("missing_video_names", [])}
        return [v for v in videos if norm_name(v.name) in norm_missing]

    def _keypoints_path(self, video_filename: str) -> str:
        """Đường dẫn file keypoints .txt đi kèm video (cùng tên gốc, trong recordings_dir)."""
        base = os.path.splitext(os.path.basename(video_filename))[0]
        return os.path.join(self._recordings_dir, f"{base}.txt")

    def upload(self, video: VideoRef, content: bytes | None = None, filename: str = "") -> None:
        # Endpoint nhận multipart 2 field BẮT BUỘC: 'video' (binary) + 'keypoints' (.txt).
        if not content:
            raise ValueError(f"upload {video.video_id}: thiếu nội dung video (fetch rỗng)")
        name = filename or video.name or f"{video.video_id}.mp4"

        kp_path = self._keypoints_path(name)
        if not os.path.exists(kp_path):
            raise ValueError(
                f"upload {video.video_id}: thiếu file keypoints {kp_path!r} "
                f"(API yêu cầu, sinh keypoints trước rồi đặt vào {self._recordings_dir!r})"
            )
        with open(kp_path, "rb") as f:
            keypoints = f.read()

        resp = self._session.post(
            self._url(f"{_BASE}/videos/upload"),
            files={
                "video": (name, content, "video/mp4"),
                "keypoints": (os.path.basename(kp_path), keypoints, "text/plain"),
            },
            timeout=600,
        )
        resp.raise_for_status()

    def trigger(self, videos: list[VideoRef], rule: RuleDef, batch_code: str) -> str:
        log.info("triggering %s", batch_code)
        resp = self._session.post(
            self._url(f"{_BASE}/videos/trigger"),
            json={
                "batch_code": batch_code,
                "videos": [
                    {"video_name": v.name, "camera_code": v.camera_id or "unknown"}
                    for v in videos
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        self._batch_rule[batch_code] = rule
        return batch_code

    @staticmethod
    def _parse_event(ev: dict) -> dict:
        """Chuẩn hoá 1 event hand-washing -> dict gọn cho validate (step/sequence/violation).

        Dữ liệu chi tiết nằm ở detection_rule_infos[].params:
            stepHandWash[{step_code,count,violation,...}] / stepSequence /
            violationHandWash / totalDurationSecond.
        """
        params: dict = {}
        for info in ev.get("detection_rule_infos") or []:
            p = info.get("params") or {}
            if p:
                params = p
                break
        steps = params.get("stepHandWash") or []
        viol = ev.get("violation")
        if viol is None:
            viol = params.get("violationHandWash")
        return {
            "event_code": ev.get("event_code", ""),
            "violation": bool(viol),
            "counts": {s.get("step_code"): s.get("count", 0) for s in steps if s.get("step_code")},
            "step_violations": {s.get("step_code"): bool(s.get("violation")) for s in steps if s.get("step_code")},
            "sequence": [int(x) for x in (params.get("stepSequence") or []) if str(x).strip().isdigit()],
            "duration": params.get("totalDurationSecond", 0),
            "original_video_path": ev.get("original_video_path", ""),
            "evidence": (ev.get("video_metadata") or {}).get("filename", ""),
        }

    @staticmethod
    def _match_name(ev: dict) -> str:
        """Tên dùng để ghép event với video ground truth: ưu tiên `original_video_path`
        (path video GỐC, khớp tên với sheet), fallback `video_metadata.filename`.
        """
        path = ev.get("original_video_path") or (ev.get("video_metadata") or {}).get("filename", "")
        return norm_name(path) if path else ""

    def _fetch_events(self, batch_code: str) -> dict[str, list[dict]]:
        """Quét toàn bộ event của batch -> {norm-name video: [event đã chuẩn hoá,...]}.

        API có thể trả list trần HOẶC dạng phân trang {total,page,page_size,data:[...]}
        — xử lý cả hai. Video không có event nào = "No Trigger".
        """
        by_video: dict[str, list[dict]] = {}
        page, page_size = 1, 200
        # while True:
        #     resp = self._session.get(
        #         self._url(f"{_BASE}/events"),
        #         params={"batch_code": batch_code, "skip": skip, "limit": limit},
        #         timeout=30,
        #     )
        #     resp.raise_for_status()
        #     body = resp.json()
        #     data = body.get("data", []) if isinstance(body, dict) else (body or [])
        #     for ev in data:
        #         name = self._match_name(ev)  # ghép theo original_video_path
        #         if not name:
        #             continue
        #         by_video.setdefault(name, []).append(self._parse_event(ev))
        #     if len(data) < limit:
        #         break
        #     skip += limit

        resp = self._session.get(
            self._url(f"{_BASE}/events"),
            params={"batch_code": batch_code, "page": page, "page_size": page_size},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", []) if isinstance(body, dict) else (body or [])
        for ev in data:
            name = self._match_name(ev)  # ghép theo original_video_path
            if not name:
                continue
            by_video.setdefault(name, []).append(self._parse_event(ev))
        return by_video

    def poll_results(self, job_id: str, videos: list[VideoRef]) -> dict[str, Prediction]:
        rule = self._batch_rule.get(job_id)
        min_wait = rule.time_sleep if rule else 0
        deadline = time.time() + self._timeout_sec
        start = time.time()
        prev: dict[str, int] | None = None
        by_video: dict[str, list[dict]] = {}

        while time.time() < deadline:
            by_video = self._fetch_events(job_id)
            elapsed = time.time() - start
            # Chữ ký = SỐ event mỗi video (không chỉ tập tên) -> 1 video nhận thêm event
            # thứ 2 cũng làm chữ ký đổi, tránh dừng sớm khi event tới rải rác.
            sig = {name: len(evs) for name, evs in by_video.items()}
            if elapsed >= min_wait and prev is not None and sig == prev:
                break
            prev = sig
            time.sleep(self._poll_interval_sec)

        results: dict[str, Prediction] = {}
        for v in videos:
            evs = by_video.get(norm_name(v.name), [])
            results[v.video_id] = Prediction(
                video_id=v.video_id,
                has_violation=any(e["violation"] for e in evs),
                triggered=bool(evs),
                events=evs,  # dữ liệu step/sequence chi tiết cho HandWashingStrategy
                raw={"batch_code": job_id},
            )
        return results
