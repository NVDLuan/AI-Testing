import cv2
import tempfile
import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from pyzbar import pyzbar
    pyzbar_available = True
except ImportError as e:
    pyzbar_available = False
    logger.warning(
        "pyzbar could not be loaded (likely due to missing zbar shared library: %s). "
        "Will fall back to OpenCV's built-in QRCodeDetector.",
        e
    )


def read_qr_in_frame(frame, qr_size=60, offset_x=5, offset_y=5, border_size=4):
    h, w = frame.shape[:2]
    # Crop với viền thừa
    x1 = w - qr_size - offset_x
    y1 = offset_y
    x2 = w - offset_x
    y2 = offset_y + qr_size

    qr_crop = frame[y1:y2, x1:x2].copy()
    # Tô đen viền (5px mỗi cạnh)
    # Viền trên
    qr_crop[0:border_size, :] = 0
    # Viền dưới
    qr_crop[-border_size:, :] = 0
    # Viền trái
    qr_crop[:, 0:border_size] = 0
    # Viền phải
    qr_crop[:, -border_size:] = 0

    if pyzbar_available:
        try:
            decoded = pyzbar.decode(qr_crop)
            if decoded:
                return decoded[0].data.decode('utf-8')
        except Exception as e:
            logger.debug("pyzbar decode failed: %s", e)

    # Fallback to OpenCV's built-in QRCodeDetector
    try:
        detector = cv2.QRCodeDetector()
        val, points, _ = detector.detectAndDecode(qr_crop)
        if val:
            return val
    except Exception as e:
        logger.debug("OpenCV QRCodeDetector failed: %s", e)

    # crop_path = "/home/viet/Pictures/qr_crop.png"
    # cv2.imwrite(crop_path, qr_crop)
    # print(f"Đã lưu crop QR: {crop_path} (size: {qr_crop.shape})")
    return None

def read_all_qr_by_video_path(type_read, video_path, qr_size=60, offset_x=5, offset_y=5, border_size=5, use_gpu: Optional[bool | str] = "auto"):
    """Read all QR codes by a video path.

    If OpenCV was built with FFMPEG support, we prefer that backend as it can
    leverage hardware decode on some systems. This is best-effort; decoding
    still gracefully falls back to CPU where appropriate.
    """
    _use_gpu = resolve_use_gpu(use_gpu)
    # Prefer FFMPEG backend when available; fallback to default
    try:
        cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap = cv2.VideoCapture(video_path)
    except Exception:
        cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Cannot open video: {video_path}")
        return None

    if type_read == "first_frame":
        while True:
            ret, frame = cap.read()
            cap.release()
            if not ret:
                print(f"Unable to read first frame of video: {video_path}")
                return None
            qr_val = read_qr_in_frame(frame, qr_size=qr_size, offset_x=offset_x, offset_y=offset_y, border_size=border_size)
            if qr_val:
                return qr_val
    else:
        list_qr_code = []
        idx = 0
        fail_count = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            qr_val = read_qr_in_frame(frame, qr_size=qr_size, offset_x=offset_x, offset_y=offset_y, border_size=border_size)
            if qr_val:
                list_qr_code.append(qr_val)
            else:
                fail_count += 1
            idx += 1
        cap.release()
        print(f"Fail Frame QR: {fail_count}. Pass Frame QR: {len(list_qr_code)}")
        return list_qr_code


def read_qr_fast(type_read, video_path, use_gpu: Optional[bool | str] = "auto"):
    qr_value = read_all_qr_by_video_path(type_read, video_path, 60, 5, 5, 4, use_gpu=use_gpu)

    if qr_value:
        try:
            frame_id = qr_value
            print(f"✔ QR Code: {qr_value} → Frame ID: {frame_id}")
            return frame_id
        except Exception as e:
            print(f"❌ Error while converting: {e}")
            return None
    return None

def read_qr_first_last(video_bytes, sample_count=20, use_gpu: Optional[bool | str] = "auto"):
    if not video_bytes:
        return None, None
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    try:
        tmp.write(video_bytes)
        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()
    first_qr = None
    last_qr = None
    try:
        # Prefer FFMPEG backend when available; fallback to default
        try:
            cap = cv2.VideoCapture(tmp_path, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(tmp_path)
        except Exception:
            cap = cv2.VideoCapture(tmp_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        # --- FIRST QR (sample đầu) ---
        for i in range(min(sample_count, total)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret:
                break

            qr_val = read_qr_in_frame(frame, 60, 5, 5, 4)
            if qr_val:
                first_qr = qr_val
                break

        # --- LAST QR (sample cuối) ---
        for i in range(total - 1, max(total - sample_count, -1), -1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret:
                continue

            qr_val = read_qr_in_frame(frame, 60, 5, 5, 4)
            if qr_val:
                last_qr = qr_val
                break

        cap.release()
        return int(first_qr), int(last_qr)
    finally:
        try:
            os.unlink(tmp_path)
        except:
            pass


def read_all_qr_from_video_bytes(video_bytes: bytes, actual_frames_map, use_gpu: Optional[bool | str] = "auto") :
    if not video_bytes:
        return []
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    try:
        tmp.write(video_bytes)
        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()
    fail_count = 0
    try:
        # Prefer FFMPEG backend when available; fallback to default
        try:
            cap = cv2.VideoCapture(tmp_path, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(tmp_path)
        except Exception:
            cap = cv2.VideoCapture(tmp_path)
        idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            qr_val = read_qr_in_frame(frame, 60, 5, 5, 4)

            # 🔥 Update actual_frames_map
            if idx in actual_frames_map:
                if qr_val:
                    actual_frames_map[idx]["frame_qr_id"] = int(qr_val)
                else:
                    actual_frames_map[idx]["frame_qr_id"] = None
                    fail_count += 1

            idx += 1
        cap.release()
        return actual_frames_map
    finally:
        try:
            print(f"Fail Frame QR: {fail_count}")
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    # Đọc QR từ video
    # video_path = "/home/viet/Downloads/event_30_08-31-46_now_30_08-34-25.mp4"
    #
    # frame_id = read_qr_fast(video_path)
    #
    # print(frame_id)
    #
    # video_path = "/home/viet/Downloads/event_30_08-31-46_now_30_08-34-11.mp4"
    # frame_id = read_qr_fast(video_path)
    #
    # print(frame_id)
    video_path = "/home/viet/Downloads/event_30_08-31-46_now_30_08-34-25.mp4"
    read_qr_fast('all', video_path)