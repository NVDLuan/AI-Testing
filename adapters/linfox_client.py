"""Client cho generic API (/api/videos, /api/evidences) — dùng cho linfox & EWS.

Quy ước có vi phạm: tồn tại evidence cho video_code đó trong batch.
"""
from __future__ import annotations

import logging
import os
import time

from configs.schema import RuleDef
from interfaces.analysis_client import AnalysisClient
from validator.models import Prediction, VideoRef

from ._http_base import BaseHttpClient, norm_name

log = logging.getLogger("ai_testing.linfox")


class LinfoxHttpClient(BaseHttpClient, AnalysisClient):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._batch_rule: dict[str, RuleDef] = {}

    def update_rule_config(self, rule: RuleDef) -> None:
        # config = nội dung <CODE>_config.json: {camera_id: {...}}
        config = {cam_id: cam.raw for cam_id, cam in rule.cameras.items()}
        if not config:
            log.warning("rule %s: không có camera config -> bỏ qua /api/rule", rule.code)
            return
        resp = self._session.post(
            self._url("/api/rule"),
            json={"rule_code": rule.code, "config": config},
            timeout=60,
        )
        resp.raise_for_status()
        log.info("rule %s: đã cập nhật config %d camera", rule.code, len(config))

    def check_missing(self, videos: list[VideoRef]) -> list[VideoRef]:
        resp = self._session.post(
            self._url("/api/videos/check"),
            json={"videos": [v.name for v in videos]},
            timeout=30,
        )
        resp.raise_for_status()
        norm_missing = {norm_name(m) for m in resp.json().get("missing_videos", [])}
        return [v for v in videos if norm_name(v.name) in norm_missing]

    def upload(self, video: VideoRef, file_path: str | None = None) -> None:
        # API nhận multipart field 'videos' (List[UploadFile]). Gửi 1 file/lần.
        if not file_path:
            raise ValueError(f"upload {video.video_id}: thiếu file_path (cần tải từ S3 trước)")
        with open(file_path, "rb") as f:
            resp = self._session.post(
                self._url("/api/videos"),
                files=[("videos", (os.path.basename(file_path), f, "video/mp4"))],
                timeout=600,
            )
        resp.raise_for_status()

    def trigger(self, videos: list[VideoRef], rule: RuleDef) -> str:
        batch_code = self.make_batch_code(rule.code or rule.name)
        videos_config: dict[str, list[str]] = {}
        for v in videos:
            videos_config.setdefault(v.camera_id or "unknown", []).append(v.name)
        resp = self._session.post(
            self._url("/api/videos/analyze"),
            json={"batch_code": batch_code, "videos_config": videos_config},
            timeout=60,
        )
        resp.raise_for_status()
        self._batch_rule[batch_code] = rule
        return batch_code

    @staticmethod
    def _evidence_keys(ev: dict) -> set[str]:
        """Khoá khớp video của một bản ghi evidence: cả `video_code` lẫn tên file
        trong `original_video_path` (đã chuẩn hoá bỏ path + đuôi).

        Response mẫu mỗi item:
            {"video_code": "571729",
             "original_video_path": "/video_data/<batch>/<cam>/571729.mp4", ...}
        """
        keys: set[str] = set()
        code = ev.get("video_code")
        if code:
            keys.add(norm_name(str(code)))
        path = ev.get("original_video_path")
        if path:
            keys.add(norm_name(str(path)))
        return keys

    @staticmethod
    def _evidence_clip_path(ev: dict) -> str:
        """Đường dẫn video evidence (clip sự kiện) để tải qua File Browser.

        Nằm ở `payload.videoMetadata.filename`, vd:
            /evidence/HM05/<cam>/<eventCode>_llava_True/event_....mp4
        """
        meta = ((ev.get("payload") or {}).get("videoMetadata") or {})
        return str(meta.get("filename") or "")

    def _fetch_evidences(self, batch_code: str, rule_code: str) -> dict[str, list[dict]]:
        """Gom toàn bộ evidence của batch -> {norm-name video: [evidence,...]}.

        Một video có >=1 evidence = có vi phạm. Nhiều evidence trùng video được gom
        chung (để báo cáo biết hệ thống bắt được mấy lần).
        """
        by_video: dict[str, list[dict]] = {}
        page = 1
        while True:
            resp = self._session.get(
                self._url("/api/evidences"),
                params={"batch_code": batch_code, "rule_code": rule_code,
                        "page": page, "page_size": 100},
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", [])
            for ev in data:
                # Trích sẵn path clip evidence ra field phẳng để EvidencePublisher dùng
                # (mặc định --evidence-field evidence_path).
                ev["evidence_path"] = self._evidence_clip_path(ev)
                for key in self._evidence_keys(ev):
                    by_video.setdefault(key, []).append(ev)
            page_size = body.get("page_size", 100) or 100
            if not data or page * page_size >= body.get("total", 0):
                break
            page += 1
        return by_video

    def poll_results(self, job_id: str, videos: list[VideoRef]) -> dict[str, Prediction]:
        rule = self._batch_rule.get(job_id)
        min_wait = rule.time_sleep if rule else 0
        deadline = time.time() + self._timeout_sec
        start = time.time()
        prev: set[str] | None = None
        by_video: dict[str, list[dict]] = {}

        while time.time() < deadline:
            by_video = self._fetch_evidences(job_id, rule.code if rule else "")
            elapsed = time.time() - start
            # Coi như xong khi đã chờ tối thiểu và tập video có evidence ổn định giữa 2 lần poll.
            if elapsed >= min_wait and prev is not None and set(by_video) == prev:
                break
            prev = set(by_video)
            time.sleep(self._poll_interval_sec)

        results: dict[str, Prediction] = {}
        for v in videos:
            evidences = by_video.get(norm_name(v.name), [])
            results[v.video_id] = Prediction(
                video_id=v.video_id,
                has_violation=bool(evidences),
                events=evidences,
                raw={"batch_code": job_id, "evidence_count": len(evidences)},
            )
        return results
