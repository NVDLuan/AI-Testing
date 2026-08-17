import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from src.lib.qr_code.test_qr import process_video
from src.utils.gpu_accel import is_ffmpeg_available


SEGMENT_PATTERN = re.compile(r"^(?P<base>.+?)_(?P<part>\d+)\.mp4$", re.IGNORECASE)


def find_segment_groups(input_dir: str) -> Tuple[Dict[str, List[Tuple[int, Path]]], List[Path]]:
    """Group split videos by base name and detect standalone videos (no underscore)."""
    base_groups: Dict[str, List[Tuple[int, Path]]] = {}
    standalone: List[Path] = []
    for path in Path(input_dir).glob("*.mp4"):
        match = SEGMENT_PATTERN.match(path.name)
        if not match:
            standalone.append(path)
            continue
        base = match.group("base")
        part = int(match.group("part"))
        base_groups.setdefault(base, []).append((part, path))

    for base, items in base_groups.items():
        items.sort(key=lambda x: x[0])
    return base_groups, standalone


def _already_processed(qr_video: Path) -> bool:
    """Check if output video already exists and non-empty."""
    return qr_video.exists() and qr_video.stat().st_size > 0


def merge_segments(base_id: str, segments: List[Tuple[int, Path]], output_dir: Path) -> Path:
    """Concatenate segments using ffmpeg concat demuxer.

    If only one segment, return it directly (no re-mux).
    """
    # Guard: single segment -> use original file
    if len(segments) == 1:
        return segments[0][1]

    # Guard: missing/empty segments
    for _, path in segments:
        if not path.exists():
            raise FileNotFoundError(f"Segment not found: {path}")
        if path.stat().st_size == 0:
            raise ValueError(f"Segment is empty: {path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{base_id}_merged.mp4"

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        for _, path in segments:
            tf.write(f"file '{path.as_posix()}'\n")
        list_path = tf.name

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            output_path.as_posix(),
        ]
        subprocess.run(cmd, check=True)
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass

    return output_path


def create_qr_video_long(
    input_dir: str,
    merged_dir: str | None = None,
    output_dir: str | None = None,
    use_gpu: bool | str = "auto",
):
    """
    - Scan input_dir for segments in form <base>_<part>.mp4
    - Merge each group back to a single video
    - Also handle normal videos (no underscore) without merging
    - Embed per-frame QR code (process_video) into the merged/standalone video
    - QR generation/overlay is forced to CPU to keep FPS/duration stable
    """
    if not is_ffmpeg_available():
        raise RuntimeError("ffmpeg is required but not available on PATH.")

    input_path = Path(input_dir)
    merged_path = Path(merged_dir) if merged_dir else input_path / "merged"
    output_path = Path(output_dir) if output_dir else input_path / "with_qr"
    merged_path.mkdir(parents=True, exist_ok=True)
    output_path.mkdir(parents=True, exist_ok=True)

    groups, standalone = find_segment_groups(str(input_path))
    if not groups and not standalone:
        print(f"No mp4 files found in {input_path}")
        return []

    results = []
    for base, segments in groups.items():
        if not segments:
            continue
        print(f"--> Processing {base}: {len(segments)} segment(s)")
        qr_video = output_path / f"{base}_qr.mp4"
        if _already_processed(qr_video):
            print(f"    Skip {base}: found existing {qr_video.name}")
            results.append(
                {
                    "base": base,
                    "segments": len(segments),
                    "merged_video": str(merged_path / f"{base}_merged.mp4"),
                    "qr_video": str(qr_video),
                    "source": "segments",
                    "skipped": True,
                }
            )
            continue
        try:
            merged_video = merge_segments(base, segments, merged_path)
            # Force CPU so QR video FPS/duration matches merged video
            process_video(str(merged_video), str(qr_video), use_gpu=False)
            results.append(
                {
                    "base": base,
                    "segments": len(segments),
                    "merged_video": str(merged_video),
                    "qr_video": str(qr_video),
                    "source": "segments",
                    "skipped": False,
                    "error": None,
                }
            )
            print(f"    Done {base}: merged -> {Path(merged_video).name}, qr -> {qr_video.name}")
        except Exception as e:
            print(f"    Error {base}: {e}")
            results.append(
                {
                    "base": base,
                    "segments": len(segments),
                    "merged_video": None,
                    "qr_video": str(qr_video),
                    "source": "segments",
                    "skipped": False,
                    "error": str(e),
                }
            )
            continue

    for path in standalone:
        base = path.stem
        print(f"--> Processing standalone video: {path.name}")
        qr_video = output_path / f"{base}_qr.mp4"
        if _already_processed(qr_video):
            print(f"    Skip {base}: found existing {qr_video.name}")
            results.append(
                {
                    "base": base,
                    "segments": 1,
                    "merged_video": None,
                    "qr_video": str(qr_video),
                    "source": "standalone",
                    "skipped": True,
                }
            )
            continue
        # Force CPU so QR video FPS/duration matches source video
        process_video(str(path), str(qr_video), use_gpu=False)
        results.append(
            {
                "base": base,
                "segments": 1,
                "merged_video": None,
                "qr_video": str(qr_video),
                "source": "standalone",
                "skipped": False,
            }
        )
        print(f"    Done {base}: qr -> {qr_video.name}")

    return results


if __name__ == "__main__":
    # Ví dụ chạy thử: chỉnh lại đường dẫn cho phù hợp
    # INPUT_DIR = "/home/viet/Videos/LFVN_LONG_VIDEO_RAW/PALLETWRONG"
    # MERGED_DIR = "/home/viet/Videos/LFVN_LONG_VIDEO_RAW/video_merged/PALLETWRONG"
    # OUTPUT_DIR = "/home/viet/Videos/LFVN_LONG_VIDEO_RAW/video_qr/PALLETWRONG"

    INPUT_DIR = "/home/qc/Videos/test"
    MERGED_DIR = "/home/qc/Videos/temp"
    OUTPUT_DIR = "/home/qc/Videos/test_QR/"

    create_qr_video_long(INPUT_DIR, MERGED_DIR, OUTPUT_DIR)
