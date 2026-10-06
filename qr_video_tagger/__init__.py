"""qr_video_tagger: nhúng frame_id dạng QR vào video và đọc lại từ video/evidence."""
from .config import DEFAULT_LAYOUT, QRLayout
from .ffmpeg import FFmpegEncoder
from .frame_capture import FrameCapture
from .generator import QRVideoGenerator
from .long_video import LongVideoQRBuilder
from .reader import QRVideoReader

__all__ = [
    "QRLayout",
    "DEFAULT_LAYOUT",
    "FFmpegEncoder",
    "QRVideoGenerator",
    "LongVideoQRBuilder",
    "QRVideoReader",
    "FrameCapture",
]

__version__ = "0.1.0"
