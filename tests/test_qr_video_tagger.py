"""Test qr_video_tagger end-to-end: sinh video mẫu bằng ffmpeg -> nhúng QR -> đọc lại.

Kiểm tra frame thứ k mang QR = k (cả video ghép từ segment, start_time != 0),
read_first_last, read_all_from_bytes, và các ca lỗi không raise.

    python -m tests.test_qr_video_tagger
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2

from qr_video_tagger import LongVideoQRBuilder, QRVideoGenerator, QRVideoReader


def make_video(path: Path, seconds: int, size: str = "640x360", fps: str = "25") -> None:
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}",
        "-f", "lavfi", "-i", f"sine=d={seconds}",
        "-t", str(seconds), "-pix_fmt", "yuv420p", "-shortest", str(path),
    ], check=True)


def read_ids(reader: QRVideoReader, path: Path) -> list:
    cap = cv2.VideoCapture(str(path))
    ids = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ids.append(reader.read_frame_id(frame))
    cap.release()
    return ids


def test_segments_and_standalone(tmp: Path) -> None:
    src = tmp / "in"
    src.mkdir()
    make_video(src / "cam_1.mp4", 2)
    make_video(src / "cam_2.mp4", 2)
    make_video(src / "event_30.mp4", 1)          # nhóm 1 file -> standalone, giữ tên
    (src / "broken.mp4").write_bytes(b"garbage")  # lỗi riêng, không dừng batch

    results = LongVideoQRBuilder(QRVideoGenerator()).run(str(src), str(tmp / "merged"), str(tmp / "qr"))
    by_base = {r["base"]: r for r in results}
    assert by_base["cam"]["source"] == "segments" and by_base["cam"]["error"] is None
    assert by_base["event_30"]["source"] == "standalone" and by_base["event_30"]["error"] is None
    assert by_base["broken"]["error"]

    reader = QRVideoReader()
    for name, n in (("cam_qr.mp4", 100), ("event_30_qr.mp4", 25)):
        ids = read_ids(reader, tmp / "qr" / name)
        assert ids == list(range(n)), f"{name}: {ids}"
        assert reader.read_first_last((tmp / "qr" / name).read_bytes()) == (0, n - 1)

    frames_map = reader.read_all_from_bytes((tmp / "qr" / "cam_qr.mp4").read_bytes(), {0: {}, 99: {}})
    assert frames_map == {0: {"frame_qr_id": 0}, 99: {"frame_qr_id": 99}}

    rerun = LongVideoQRBuilder(QRVideoGenerator()).run(str(src), str(tmp / "merged"), str(tmp / "qr"))
    assert all(r["skipped"] for r in rerun if not r["error"])
    print("ok segments/standalone")


def test_segment_mismatch(tmp: Path) -> None:
    src = tmp / "bad"
    src.mkdir()
    make_video(src / "x_1.mp4", 1, size="640x360")
    make_video(src / "x_2.mp4", 1, size="320x240")
    (result,) = LongVideoQRBuilder().run(str(src), str(tmp / "m2"), str(tmp / "q2"))
    assert "không cùng thông số" in result["error"]
    print("ok segment mismatch")


def test_reader_no_qr(tmp: Path) -> None:
    plain = tmp / "plain.mp4"
    make_video(plain, 1)
    reader = QRVideoReader()
    assert reader.read_first_last(plain.read_bytes()) == (None, None)
    assert reader.read_first_last(b"") == (None, None)
    assert reader.read_first_last(b"garbage") == (None, None)
    assert reader.read_all_from_bytes(b"", {0: {}}) == {0: {}}
    assert reader.read_all_from_bytes(plain.read_bytes(), {0: {}}) == {0: {"frame_qr_id": None}}
    print("ok reader without QR")


class _FakeStore:
    """VideoStore giả: trả bytes theo video_id, đếm số lần fetch."""

    def __init__(self, videos: dict[str, bytes]):
        self.videos = videos
        self.fetches = 0

    def fetch(self, video):
        self.fetches += 1
        return self.videos[video.video_id], f"{video.name}.mp4"


class _FakeClient:
    def __init__(self):
        self.uploads = {}

    def upload(self, video, content, filename):
        self.uploads[video.video_id] = (content, filename)


def _ctx(store, missing):
    import logging
    from types import SimpleNamespace

    return SimpleNamespace(
        missing=missing, video_store=store, client=_FakeClient(), qr_videos={}, qr_tmp_dir="",
        aborted=False, log=logging.getLogger("test_qr"),
    )


def test_pipeline_handler(tmp: Path) -> None:
    from pipeline.handlers import GenerateQRCodeIntoVideoHandler, UploadHandler
    from validator.models import VideoRef

    good = tmp / "good.mp4"
    make_video(good, 1)
    videos = [
        VideoRef(video_id="USEPHONE/good", url="s3://b/good.mp4", name="good"),
        VideoRef(video_id="USEPHONE/broken", url="s3://b/broken.mp4", name="broken"),
        VideoRef(video_id="USEPHONE/mock", url="s3://b/mock.mp4", name="mock"),
    ]
    store = _FakeStore({"USEPHONE/good": good.read_bytes(), "USEPHONE/broken": b"garbage", "USEPHONE/mock": b""})
    reader = QRVideoReader()

    # Không cache_dir: thư mục tạm, Upload xoá sau khi xong
    ctx = _ctx(store, videos)
    GenerateQRCodeIntoVideoHandler().process(ctx)
    tmp_dir = ctx.qr_tmp_dir
    assert set(ctx.qr_videos) == {"USEPHONE/good"} and Path(tmp_dir).is_dir()
    UploadHandler().process(ctx)
    content, filename = ctx.client.uploads["USEPHONE/good"]
    assert filename == "good.mp4" and reader.read_first_last(content) == (0, 24)
    assert ctx.client.uploads["USEPHONE/broken"] == (b"garbage", "broken.mp4")  # lỗi -> upload bản gốc
    assert ctx.client.uploads["USEPHONE/mock"] == (None, "mock.mp4")             # mock -> như cũ
    assert not Path(tmp_dir).exists() and ctx.qr_tmp_dir == ""

    # Có cache_dir: lần 2 tái dùng bản QR, không fetch lại video đã có QR
    cache = tmp / "cache"
    GenerateQRCodeIntoVideoHandler(work_dir=str(cache)).process(_ctx(store, videos))
    assert (cache / "qr" / "USEPHONE" / "good" / "good.mp4").is_file()
    store.fetches = 0
    ctx = _ctx(store, videos)
    GenerateQRCodeIntoVideoHandler(work_dir=str(cache)).process(ctx)
    assert store.fetches == 2  # chỉ broken + mock, good lấy từ cache
    UploadHandler().process(ctx)
    assert reader.read_first_last(ctx.client.uploads["USEPHONE/good"][0]) == (0, 24)
    print("ok pipeline handler")


if __name__ == "__main__":
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("cần ffmpeg + ffprobe trên PATH")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        test_segments_and_standalone(root)
        test_segment_mismatch(root)
        test_reader_no_qr(root)
        test_pipeline_handler(root)
    print("all passed")
