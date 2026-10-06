"""Phát hiện ffmpeg / NVIDIA GPU và chọn encoder (tách từ src.utils.gpu_accel)."""
from __future__ import annotations

import functools
import shutil
import subprocess
from typing import List, Optional, Tuple


class FFmpegEncoder:
    """Chọn encoder ffmpeg: h264_nvenc nếu có GPU, ngược lại libx264."""

    GPU_ENCODER: Tuple[str, str] = ("h264_nvenc", "p3")
    CPU_ENCODER: Tuple[str, str] = ("libx264", "fast")
    QUALITY = 23

    @staticmethod
    @functools.lru_cache(maxsize=1)
    def is_ffmpeg_available() -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    @staticmethod
    def _run_capture(cmd: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, check=False, capture_output=True, text=True)

    @classmethod
    @functools.lru_cache(maxsize=1)
    def detect_gpu_support(cls) -> Tuple[bool, str]:
        """Kiểm tra ffmpeg có dùng được NVIDIA GPU (NVENC + CUDA) không. Cache sau lần đầu."""
        if shutil.which("nvidia-smi") is None:
            return False, "nvidia-smi not available"
        if cls._run_capture(["nvidia-smi"]).returncode != 0:
            return False, "nvidia-smi failed"
        if not cls.is_ffmpeg_available():
            return False, "ffmpeg not available"
        if "h264_nvenc" not in cls._run_capture(["ffmpeg", "-encoders"]).stdout:
            return False, "ffmpeg nvenc encoder not found"
        if "cuda" not in cls._run_capture(["ffmpeg", "-hwaccels"]).stdout:
            return False, "ffmpeg cuda hwaccel not found"
        return True, "gpu supported"

    @classmethod
    def resolve_use_gpu(cls, user_choice: Optional[bool | str]) -> bool:
        """None/"auto"/True -> dùng GPU nếu có; False -> ép CPU."""
        if user_choice is False:
            return False
        return cls.detect_gpu_support()[0]

    @classmethod
    def select(cls, use_gpu: Optional[bool | str] = "auto") -> Tuple[str, str]:
        """Trả về (encoder, preset). Không có ffmpeg hoặc không có GPU -> libx264/fast."""
        if not cls.is_ffmpeg_available():
            return cls.CPU_ENCODER
        return cls.GPU_ENCODER if cls.resolve_use_gpu(use_gpu) else cls.CPU_ENCODER

    @classmethod
    def encode_args(cls, use_gpu: Optional[bool | str] = "auto") -> List[str]:
        """Tham số encode đầy đủ. NVENC không hỗ trợ -crf nên dùng -cq tương đương."""
        encoder, preset = cls.select(use_gpu)
        quality_flag = "-cq" if "nvenc" in encoder else "-crf"
        return ["-c:v", encoder, "-preset", preset, quality_flag, str(cls.QUALITY), "-pix_fmt", "yuv420p"]
