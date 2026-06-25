"""Trích xuất keypoints hàng loạt cho video trong một thư mục → file .txt.

Mỗi video `foo.mp4` sẽ sinh ra `foo.txt` cùng thư mục. Video nào đã có file
`.txt` tương ứng thì bỏ qua (trừ khi truyền --overwrite).

Chạy song song bằng multiprocessing: mỗi process khởi tạo MediaPipe Holistic
riêng, nên các video được xử lý thực sự song song (global lock trong
`mediapipe_1.py` chỉ serialize trong phạm vi từng process).

Dùng:
    python -m app.modules.hand_washing.process_recordings
    python -m app.modules.hand_washing.process_recordings /data/recording --workers 4
    python -m app.modules.hand_washing.process_recordings /data/recording --overwrite
"""

from __future__ import annotations

import argparse
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

from utils.mediapipe_1 import KeypointExtractor

DEFAULT_DIR = "downloads/video_raws"
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".m4v"}

log = logging.getLogger("process_recordings")


def _output_path(video_path: str) -> str:
    """Đường dẫn file .txt keypoints tương ứng với video."""
    stem, _ = os.path.splitext(video_path)
    return f"{stem}.txt"


def _process_one(video_path: str, overwrite: bool) -> tuple[str, str]:
    """Xử lý 1 video. Trả về (video_path, status) với status thuộc
    {"skipped", "done", "failed"}.

    Import MediaPipe được đặt bên trong worker để mỗi process có Holistic riêng.
    """
    output_path = _output_path(video_path)

    if not overwrite and os.path.exists(output_path):
        return video_path, "skipped"

    result_path = KeypointExtractor().extract_keypoints(video_path)
    if not result_path:
        return video_path, "failed"

    # extract_keypoints sinh ra "<stem>_keypoints.txt"; chuẩn hoá về "<stem>.txt"
    if os.path.abspath(result_path) != os.path.abspath(output_path):
        os.replace(result_path, output_path)

    return video_path, "done"


def _find_videos(directory: str) -> list[str]:
    """Tìm đệ quy mọi file video trong thư mục."""
    videos: list[str] = []
    for root, _, files in os.walk(directory):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                videos.append(os.path.join(root, name))
    return sorted(videos)


def process_directory(directory: str, workers: int, overwrite: bool) -> dict[str, int]:
    """Xử lý toàn bộ video trong thư mục, trả về thống kê số lượng theo status."""
    videos = _find_videos(directory)
    counts = {"done": 0, "skipped": 0, "failed": 0}

    if not videos:
        log.info("Không tìm thấy video nào trong %s", directory)
        return counts

    workers = max(1, min(workers, len(videos)))
    log.info(
        "Xử lý %d video với %d process (overwrite=%s)", len(videos), workers, overwrite
    )

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_process_one, video, overwrite): video for video in videos
        }
        for future in as_completed(futures):
            video = futures[future]
            try:
                _, status = future.result()
            except Exception:
                log.exception("Lỗi xử lý %s", video)
                counts["failed"] += 1
                continue
            counts[status] += 1
            log.info("[%s] %s", status, video)

    log.info(
        "Hoàn tất: %d done, %d skipped, %d failed",
        counts["done"],
        counts["skipped"],
        counts["failed"],
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trích xuất keypoints hàng loạt video trong thư mục → .txt"
    )
    parser.add_argument(
        "directory", nargs="?", default=DEFAULT_DIR, help=f"Thư mục chứa video (mặc định: {DEFAULT_DIR})"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Số process chạy song song (mặc định: số CPU)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Xử lý lại cả khi file .txt đã tồn tại",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(processName)s] %(message)s"
    )

    if not os.path.isdir(args.directory):
        raise SystemExit(f"Thư mục không tồn tại: {args.directory}")

    process_directory(args.directory, args.workers, args.overwrite)


if __name__ == "__main__":
    main()
