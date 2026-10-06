"""Đọc QR (frame_id) đã nhúng ở góc trên-phải của video."""
from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Iterator, List, Optional, Tuple

import cv2

from .config import DEFAULT_LAYOUT, QRLayout

logger = logging.getLogger(__name__)

try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError as e:
    pyzbar = None
    PYZBAR_AVAILABLE = False
    logger.warning(
        "pyzbar could not be loaded (likely due to missing zbar shared library: %s). "
        "Will fall back to OpenCV's built-in QRCodeDetector.",
        e,
    )


def to_frame_id(value) -> Optional[int]:
    """Chuyển nội dung QR sang int; QR rỗng/không phải số -> None (không raise)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class QRVideoReader:
    """Decode QR: thử pyzbar trước, fallback cv2.QRCodeDetector."""

    def __init__(self, layout: QRLayout = DEFAULT_LAYOUT):
        self.layout = layout
        self._detector = cv2.QRCodeDetector()

    # ------------------------------------------------------------------ frame
    def read_frame(self, frame) -> Optional[str]:
        """Crop vùng QR, tô đen viền rồi decode. Trả về chuỗi QR hoặc None."""
        if frame is None:
            return None
        lo = self.layout
        h, w = frame.shape[:2]
        x1 = max(w - lo.crop_size - lo.crop_offset_x, 0)
        y1 = lo.crop_offset_y
        x2 = w - lo.crop_offset_x
        y2 = min(lo.crop_offset_y + lo.crop_size, h)
        if x2 <= x1 or y2 <= y1:
            return None

        qr_crop = frame[y1:y2, x1:x2].copy()
        b = lo.border_size
        if b > 0:
            qr_crop[0:b, :] = 0
            qr_crop[-b:, :] = 0
            qr_crop[:, 0:b] = 0
            qr_crop[:, -b:] = 0

        if PYZBAR_AVAILABLE:
            try:
                decoded = pyzbar.decode(qr_crop)
                if decoded:
                    return decoded[0].data.decode("utf-8")
            except Exception as e:
                logger.debug("pyzbar decode failed: %s", e)

        try:
            val, _points, _ = self._detector.detectAndDecode(qr_crop)
            if val:
                return val
        except Exception as e:
            logger.debug("OpenCV QRCodeDetector failed: %s", e)

        return None

    def read_frame_id(self, frame) -> Optional[int]:
        return to_frame_id(self.read_frame(frame))

    # ------------------------------------------------------------ video path
    def read_by_path(self, type_read: str, video_path: str):
        """
        type_read == "first_frame": đọc frame đầu -> str | None
        khác: đọc toàn bộ frame -> list QR đọc được (bỏ frame lỗi)
        """
        cap = self._open_capture(video_path)
        if not cap.isOpened():
            print(f"Cannot open video: {video_path}")
            return None

        try:
            if type_read == "first_frame":
                ret, frame = cap.read()
                if not ret:
                    print(f"Unable to read first frame of video: {video_path}")
                    return None
                return self.read_frame(frame)

            list_qr_code: List[str] = []
            fail_count = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                qr_val = self.read_frame(frame)
                if qr_val:
                    list_qr_code.append(qr_val)
                else:
                    fail_count += 1
            print(f"Fail Frame QR: {fail_count}. Pass Frame QR: {len(list_qr_code)}")
            return list_qr_code
        finally:
            cap.release()

    def read_fast(self, type_read: str, video_path: str):
        """Như read_by_path nhưng trả về frame_id dạng int (first_frame) hoặc list[int] (all)."""
        qr_value = self.read_by_path(type_read, video_path)
        if not qr_value:
            return None
        if isinstance(qr_value, list):
            frame_ids = [fid for fid in (to_frame_id(v) for v in qr_value) if fid is not None]
            print(f"✔ QR Code: {len(frame_ids)} frame(s), range [{min(frame_ids, default=None)}, "
                  f"{max(frame_ids, default=None)}]")
            return frame_ids
        frame_id = to_frame_id(qr_value)
        if frame_id is None:
            print(f"❌ QR không phải frame id: {qr_value!r}")
            return None
        print(f"✔ QR Code: {qr_value} → Frame ID: {frame_id}")
        return frame_id

    # ----------------------------------------------------------- video bytes
    def read_first_last(self, video_bytes: bytes, sample_count: int = 20) -> Tuple[Optional[int], Optional[int]]:
        """
        Quét tối đa `sample_count` frame đầu (xuôi) và cuối (ngược),
        lấy frame_id đọc được đầu tiên ở mỗi phía -> (first, last); phía nào không đọc được -> None.
        """
        if not video_bytes:
            return None, None

        with self._temp_video(video_bytes) as tmp_path:
            cap = self._open_capture(tmp_path)
            try:
                if not cap.isOpened():
                    return None, None
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if total <= 0:
                    return None, None

                first_qr = None
                for i in range(min(sample_count, total)):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                    ret, frame = cap.read()
                    if not ret:
                        break
                    first_qr = self.read_frame_id(frame)
                    if first_qr is not None:
                        break

                last_qr = None
                for i in range(total - 1, max(total - sample_count, -1), -1):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                    ret, frame = cap.read()
                    if not ret:
                        continue
                    last_qr = self.read_frame_id(frame)
                    if last_qr is not None:
                        break

                return first_qr, last_qr
            finally:
                cap.release()

    def read_all_from_bytes(self, video_bytes: bytes, actual_frames_map: dict) -> dict:
        """Đọc tuần tự mọi frame, gán actual_frames_map[idx]["frame_qr_id"] = int | None."""
        if not video_bytes:
            return actual_frames_map

        fail_count = 0
        with self._temp_video(video_bytes) as tmp_path:
            cap = self._open_capture(tmp_path)
            try:
                idx = 0
                while cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        break
                    if idx in actual_frames_map:
                        frame_id = self.read_frame_id(frame)
                        actual_frames_map[idx]["frame_qr_id"] = frame_id
                        if frame_id is None:
                            fail_count += 1
                    idx += 1
                return actual_frames_map
            finally:
                cap.release()
                print(f"Fail Frame QR: {fail_count}")

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _open_capture(path: str) -> cv2.VideoCapture:
        """Ưu tiên backend FFMPEG, không mở được thì dùng backend mặc định."""
        try:
            cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(path)
        except Exception:
            cap = cv2.VideoCapture(path)
        return cap

    @staticmethod
    @contextmanager
    def _temp_video(video_bytes: bytes) -> Iterator[str]:
        fd, path = tempfile.mkstemp(suffix=".mp4")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(video_bytes)
            yield path
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
