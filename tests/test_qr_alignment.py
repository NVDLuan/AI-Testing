"""Test chấm thời điểm vi phạm bằng QR: QRAlignmentStrategy (thuần) + ReadEvidenceQRHandler (e2e).

    python -m tests.test_qr_alignment
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from validator.models import GroundTruthRecord, Prediction
from validator.qr_alignment_strategy import (
    EVENT_QR_FIRST,
    EVENT_QR_LAST,
    QRAlignmentStrategy,
    parse_expected_windows,
    time_to_seconds,
)
from validator.validator import validate

WINDOW_1S_2S = '[{"eventStart": "00:01", "eventEnd": "00:02", "area": [0, 0, 10, 10]}]'


def _gt(expected: bool, expected_result: str | None = WINDOW_1S_2S) -> GroundTruthRecord:
    raw = {} if expected_result is None else {"ExpectedResult": expected_result}
    return GroundTruthRecord(video_id="R/v", video_url="s3://b/v.mp4", expected_violation=expected, raw=raw)


def _pred(*qr_ranges) -> Prediction:
    events = [{"evidence": f"/e/{i}.mp4", EVENT_QR_FIRST: r[0], EVENT_QR_LAST: r[1]} for i, r in enumerate(qr_ranges)]
    return Prediction(video_id="R/v", has_violation=bool(events), events=events)


def test_parsing() -> None:
    assert time_to_seconds("01:30") == 90 and time_to_seconds("1:00:05") == 3605
    assert time_to_seconds(12) == 12 and time_to_seconds("abc") is None and time_to_seconds("") is None
    assert parse_expected_windows(WINDOW_1S_2S, 25) == [(25, 50)]
    # python-literal (sheet hay dán kiểu này), dict đơn, đảo ngược start/end, entry hỏng bị bỏ
    assert parse_expected_windows("[{'eventStart': '00:04', 'eventEnd': '00:03'}, {'eventStart': 'x'}]", 25) == [(75, 100)]
    assert parse_expected_windows({"eventStart": "00:02", "eventEnd": "00:02"}, 10) == [(20, 20)]
    assert parse_expected_windows("", 25) == [] and parse_expected_windows("not json", 25) == []
    print("ok parsing")


def test_strategy() -> None:
    s = QRAlignmentStrategy(fps=25, tolerance_sec=1.0)  # tolerance = 25 frame
    assert s.classify(_gt(True), _pred((30, 40))) == ["TP"]                 # nằm trong cửa sổ
    assert s.classify(_gt(True), _pred((70, 90))) == ["TP"]                 # lệch 20 frame, trong dung sai
    assert s.classify(_gt(True), _pred((200, 250))) == ["FN", "FP"]         # sai thời điểm
    assert s.classify(_gt(True), _pred((200, 250), (45, 60))) == ["TP"]     # 1 clip đúng là đủ
    assert s.classify(_gt(True), _pred()) == ["FN"]
    assert s.classify(_gt(False), _pred((30, 40))) == ["FP"]
    assert s.classify(_gt(False), _pred()) == ["TN"]
    # Không verify được -> như binary, detail ghi lý do
    assert s.classify(_gt(True, None), _pred((200, 250))) == ["TP"]
    no_qr = Prediction(video_id="R/v", has_violation=True, events=[{"evidence": "/e/x.mp4"}])
    assert s.classify(_gt(True), no_qr) == ["TP"]
    assert s.detail(_gt(True), no_qr)["qr_status"] == "no_qr_in_evidence"

    d = s.detail(_gt(True), _pred((200, 250)))
    assert d["qr_status"] == "evidence_out_of_window" and d["expected_windows_sec"] == [[1.0, 2.0]]
    assert d["evidence_qr_ranges_sec"] == [[8.0, 10.0]]
    assert s.predicted_label(_gt(True), _pred((200, 250))) == "Violation (sai thời điểm)"
    assert s.predicted_label(_gt(True), _pred((30, 40))) == "Violation (đúng thời điểm)"

    # Qua lõi validate(): ["FN","FP"] bump 2 đơn vị
    rep = validate([_gt(True)], {"R/v": _pred((200, 250))}, s)
    assert (rep.overall.tp, rep.overall.fp, rep.overall.fn) == (0, 1, 1)
    print("ok strategy")


class _ClipFetcher:
    def __init__(self, clips: dict[str, bytes]):
        self.clips = clips
        self.calls = 0

    def fetch(self, ref):
        self.calls += 1
        if ref == "/boom.mp4":
            raise RuntimeError("file-browser 500")
        return self.clips[ref], ref.rsplit("/", 1)[-1]


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def test_handler_end_to_end(tmp: Path) -> None:
    from pipeline.handlers import ReadEvidenceQRHandler
    from qr_video_tagger import QRVideoGenerator

    src = tmp / "src.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-t", "4", "-pix_fmt", "yuv420p", str(src))
    qr_src = tmp / "src_qr.mp4"
    QRVideoGenerator(use_gpu=False).process_video(str(src), str(qr_src))
    # Clip evidence giả: cắt giây 1->2 (frame gốc 25..49) và giây 3->4 (75..99), encode lại như AI làm
    clip_ok, clip_late = tmp / "ok.mp4", tmp / "late.mp4"
    _ffmpeg("-i", str(qr_src), "-vf", "trim=start_frame=25:end_frame=50,setpts=PTS-STARTPTS", str(clip_ok))
    _ffmpeg("-i", str(qr_src), "-vf", "trim=start_frame=75:end_frame=100,setpts=PTS-STARTPTS", str(clip_late))

    fetcher = _ClipFetcher({"/ok.mp4": clip_ok.read_bytes(), "/late.mp4": clip_late.read_bytes(),
                            "/plain.mp4": src.read_bytes()})
    preds = {
        "R/ok": Prediction("R/ok", True, events=[{"evidence": "/ok.mp4"}, {"evidence": "/ok.mp4"}]),
        "R/late": Prediction("R/late", True, events=[{"evidence": "/late.mp4"}]),
        "R/plain": Prediction("R/plain", True, events=[{"evidence": "/plain.mp4"}, {"evidence": "/boom.mp4"}]),
    }
    ctx = SimpleNamespace(evidence_fetcher=fetcher, predictions=preds, log=logging.getLogger("test_qr"))
    ReadEvidenceQRHandler().process(ctx)

    ok_ev = preds["R/ok"].events
    assert (ok_ev[0][EVENT_QR_FIRST], ok_ev[0][EVENT_QR_LAST]) == (25, 49), ok_ev[0]
    assert ok_ev[1][EVENT_QR_FIRST] == 25 and fetcher.calls == 4  # /ok.mp4 chỉ tải 1 lần (ok, late, plain, boom)
    late_ev = preds["R/late"].events[0]
    assert (late_ev[EVENT_QR_FIRST], late_ev[EVENT_QR_LAST]) == (75, 99), late_ev
    plain = preds["R/plain"].events
    assert plain[0][EVENT_QR_FIRST] is None and plain[1][EVENT_QR_FIRST] is None  # không QR / lỗi tải

    # Chấm với sheet khai vi phạm ở giây 1->2, dung sai 0
    s = QRAlignmentStrategy(fps=25, tolerance_sec=0)
    gts = [GroundTruthRecord(video_id=v, video_url="", expected_violation=True, raw={"ExpectedResult": WINDOW_1S_2S})
           for v in preds]
    rep = validate(gts, preds, s)
    by_video = {}
    for c in rep.comparisons:
        by_video.setdefault(c.video_id, []).append(c.outcome)
    assert by_video == {"R/ok": ["TP"], "R/late": ["FN", "FP"], "R/plain": ["TP"]}, by_video
    print("ok handler end-to-end")


def test_handler_skips_without_fetcher() -> None:
    from pipeline.handlers import ReadEvidenceQRHandler

    preds = {"R/v": Prediction("R/v", True, events=[{"evidence": "/x.mp4"}])}
    ReadEvidenceQRHandler().process(SimpleNamespace(evidence_fetcher=None, predictions=preds,
                                                    log=logging.getLogger("test_qr")))
    assert EVENT_QR_FIRST not in preds["R/v"].events[0]
    print("ok handler skip")


if __name__ == "__main__":
    test_parsing()
    test_strategy()
    test_handler_skips_without_fetcher()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("cần ffmpeg + ffprobe trên PATH cho test end-to-end")
    with tempfile.TemporaryDirectory() as d:
        test_handler_end_to_end(Path(d))
    print("all passed")
