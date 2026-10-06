"""Video dài bị cắt thành nhiều đoạn <base>_<part>.mp4: ghép lại rồi nhúng QR."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .ffmpeg import FFmpegEncoder
from .generator import QRVideoGenerator


class LongVideoQRBuilder:
    """
    - Quét input_dir, gom các segment <base>_<part>.mp4 theo base (sort theo part)
    - Chỉ nhóm có >= 2 file mới được coi là video bị cắt; nhóm 1 file (vd. `event_30.mp4`)
      được xử lý như standalone và giữ nguyên tên gốc
    - Ghép mỗi nhóm bằng ffmpeg concat demuxer (-c copy) sau khi kiểm tra các segment cùng thông số
    - Nhúng QR từng frame; QR luôn ép CPU để FPS/thời lượng khớp video nguồn
    - Bỏ qua video đã có output <base>_qr.mp4 (không rỗng)
    - Lỗi ở 1 video không làm dừng cả batch; lỗi được ghi vào result["error"]
    """

    SEGMENT_PATTERN = re.compile(r"^(?P<base>.+?)_(?P<part>\d+)\.mp4$", re.IGNORECASE)
    # Các thuộc tính phải giống nhau để concat -c copy cho ra video hợp lệ
    _CONCAT_KEYS = ("codec_name", "profile", "width", "height", "pix_fmt", "r_frame_rate", "time_base")

    def __init__(self, generator: Optional[QRVideoGenerator] = None):
        self.generator = generator or QRVideoGenerator()

    # ------------------------------------------------------------------ steps
    @classmethod
    def find_segment_groups(cls, input_dir: str) -> Tuple[Dict[str, List[Tuple[int, Path]]], List[Path]]:
        base_groups: Dict[str, List[Tuple[int, Path]]] = {}
        standalone: List[Path] = []
        for path in sorted(Path(input_dir).glob("*.mp4")):
            match = cls.SEGMENT_PATTERN.match(path.name)
            if not match:
                standalone.append(path)
                continue
            base_groups.setdefault(match.group("base"), []).append((int(match.group("part")), path))

        groups: Dict[str, List[Tuple[int, Path]]] = {}
        for base, items in base_groups.items():
            if len(items) == 1:
                standalone.append(items[0][1])
                continue
            groups[base] = sorted(items, key=lambda x: x[0])
        return groups, standalone

    @staticmethod
    def already_processed(qr_video: Path) -> bool:
        return qr_video.exists() and qr_video.stat().st_size > 0

    @classmethod
    def _stream_signature(cls, path: Path) -> Tuple:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
               "-select_streams", "v:0", str(path)]
        streams = json.loads(subprocess.check_output(cmd)).get("streams", [])
        if not streams:
            raise ValueError(f"Segment has no video stream: {path}")
        return tuple(streams[0].get(k) for k in cls._CONCAT_KEYS)

    @classmethod
    def merge_segments(cls, base_id: str, segments: List[Tuple[int, Path]], output_dir: Path) -> Path:
        """Ghép segment bằng concat demuxer. Chỉ 1 segment -> trả về file gốc."""
        if len(segments) == 1:
            return segments[0][1]

        for _, path in segments:
            if not path.exists():
                raise FileNotFoundError(f"Segment not found: {path}")
            if path.stat().st_size == 0:
                raise ValueError(f"Segment is empty: {path}")

        ref_sig = cls._stream_signature(segments[0][1])
        for _, path in segments[1:]:
            sig = cls._stream_signature(path)
            if sig != ref_sig:
                diff = {k: (a, b) for k, a, b in zip(cls._CONCAT_KEYS, ref_sig, sig) if a != b}
                raise ValueError(f"Segment {path.name} không cùng thông số với {segments[0][1].name}: {diff}")

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{base_id}_merged.mp4"

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
            for _, path in segments:
                # concat demuxer: escape dấu ' trong path thành '\''
                escaped = path.resolve().as_posix().replace("'", "'\\''")
                tf.write(f"file '{escaped}'\n")
            list_path = tf.name

        try:
            subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat",
                "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                output_path.as_posix(),
            ], check=True)
        finally:
            try:
                os.unlink(list_path)
            except OSError:
                pass

        return output_path

    # ----------------------------------------------------------------- public
    def run(self, input_dir: str, merged_dir: str | None = None, output_dir: str | None = None) -> List[dict]:
        if not FFmpegEncoder.is_ffmpeg_available():
            raise RuntimeError("ffmpeg/ffprobe is required but not available on PATH.")

        input_path = Path(input_dir)
        merged_path = Path(merged_dir) if merged_dir else input_path / "merged"
        output_path = Path(output_dir) if output_dir else input_path / "with_qr"
        merged_path.mkdir(parents=True, exist_ok=True)
        output_path.mkdir(parents=True, exist_ok=True)

        groups, standalone = self.find_segment_groups(str(input_path))
        if not groups and not standalone:
            print(f"No mp4 files found in {input_path}")
            return []

        results: List[dict] = []
        for base, segments in groups.items():
            results.append(self._process_group(base, segments, merged_path, output_path))
        for path in standalone:
            results.append(self._process_standalone(path, output_path))

        failed = [r["base"] for r in results if r.get("error")]
        print(f"Tổng: {len(results)} | Lỗi: {len(failed)}" + (f" -> {failed}" if failed else ""))
        return results

    # ---------------------------------------------------------------- helpers
    def _process_group(self, base: str, segments: List[Tuple[int, Path]], merged_path: Path,
                       output_path: Path) -> dict:
        print(f"--> Processing {base}: {len(segments)} segment(s)")
        qr_video = output_path / f"{base}_qr.mp4"
        result = {"base": base, "segments": len(segments), "qr_video": str(qr_video), "source": "segments"}

        if self.already_processed(qr_video):
            print(f"    Skip {base}: found existing {qr_video.name}")
            merged = merged_path / f"{base}_merged.mp4"
            return {**result, "merged_video": str(merged) if merged.exists() else None,
                    "skipped": True, "error": None}

        try:
            merged_video = self.merge_segments(base, segments, merged_path)
            # Ép CPU để FPS/thời lượng video QR khớp video đã ghép
            self.generator.process_video(str(merged_video), str(qr_video), use_gpu=False)
            print(f"    Done {base}: merged -> {Path(merged_video).name}, qr -> {qr_video.name}")
            return {**result, "merged_video": str(merged_video), "skipped": False, "error": None}
        except Exception as e:
            print(f"    Error {base}: {e}")
            self._remove_partial(qr_video)
            return {**result, "merged_video": None, "skipped": False, "error": str(e)}

    def _process_standalone(self, path: Path, output_path: Path) -> dict:
        base = path.stem
        print(f"--> Processing standalone video: {path.name}")
        qr_video = output_path / f"{base}_qr.mp4"
        result = {"base": base, "segments": 1, "merged_video": None, "qr_video": str(qr_video),
                  "source": "standalone"}

        if self.already_processed(qr_video):
            print(f"    Skip {base}: found existing {qr_video.name}")
            return {**result, "skipped": True, "error": None}

        try:
            # Ép CPU để FPS/thời lượng video QR khớp video nguồn
            self.generator.process_video(str(path), str(qr_video), use_gpu=False)
            print(f"    Done {base}: qr -> {qr_video.name}")
            return {**result, "skipped": False, "error": None}
        except Exception as e:
            print(f"    Error {base}: {e}")
            self._remove_partial(qr_video)
            return {**result, "skipped": False, "error": str(e)}

    @staticmethod
    def _remove_partial(qr_video: Path) -> None:
        """Xóa output dở dang để lần chạy sau không bị skip nhầm."""
        try:
            qr_video.unlink(missing_ok=True)
        except OSError:
            pass
