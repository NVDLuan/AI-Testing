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
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._batch_rule: dict[str, RuleDef] = {}

    def check_missing(self, videos: list[VideoRef]) -> list[VideoRef]:
        resp = self._session.post(
            self._url(f"{_BASE}/videos/check"),
            json={"video_names": [v.name for v in videos]},
            timeout=30,
        )
        resp.raise_for_status()
        norm_missing = {norm_name(m) for m in resp.json().get("missing_video_names", [])}
        return [v for v in videos if norm_name(v.name) in norm_missing]

    def upload(self, video: VideoRef, file_path: str | None = None) -> None:
        # Endpoint nhận multipart 'file' (binary, 1 file/lần).
        if not file_path:
            raise ValueError(f"upload {video.video_id}: thiếu file_path (cần tải từ S3 trước)")
        with open(file_path, "rb") as f:
            resp = self._session.post(
                self._url(f"{_BASE}/videos/upload"),
                files={"file": (os.path.basename(file_path), f, "video/mp4")},
                timeout=600,
            )
        resp.raise_for_status()

    def trigger(self, videos: list[VideoRef], rule: RuleDef) -> str:
        batch_code = self.make_batch_code(rule.code or rule.name or "HW")
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

    def _fetch_violation_videos(self, batch_code: str) -> set[str]:
        """Lấy tập norm-name video có event.violation == True."""
        positives: set[str] = set()
        skip = 0
        while True:
            resp = self._session.get(
                self._url(f"{_BASE}/events"),
                params={"batch_code": batch_code, "skip": skip, "limit": 100},
                timeout=30,
            )
            resp.raise_for_status()
            events = resp.json() or []
            for ev in events:
                if ev.get("violation"):
                    fname = (ev.get("video_metadata") or {}).get("filename", "")
                    if fname:
                        positives.add(norm_name(fname))
            if len(events) < 100:
                break
            skip += 100
        return positives

    def poll_results(self, job_id: str, videos: list[VideoRef]) -> dict[str, Prediction]:
        rule = self._batch_rule.get(job_id)
        min_wait = rule.time_sleep if rule else 0
        deadline = time.time() + self._timeout_sec
        start = time.time()
        prev: set[str] | None = None
        positives: set[str] = set()

        while time.time() < deadline:
            positives = self._fetch_violation_videos(job_id)
            elapsed = time.time() - start
            if elapsed >= min_wait and prev is not None and positives == prev:
                break
            prev = positives
            time.sleep(self._poll_interval_sec)

        return {
            v.video_id: Prediction(
                video_id=v.video_id,
                has_violation=norm_name(v.name) in positives,
                raw={"batch_code": job_id},
            )
            for v in videos
        }
