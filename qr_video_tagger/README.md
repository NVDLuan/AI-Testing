# qr-video-tagger

Nhúng QR chứa `frame_id` vào góc trên-phải từng frame video, và đọc lại để map evidence về frame gốc.

Yêu cầu hệ thống: `ffmpeg`, `ffprobe` trên PATH; `libzbar0` (nếu thiếu sẽ fallback OpenCV).

```bash
.venv/bin/pip install -r requirements.txt   # qrcode, pyzbar, pillow, opencv-python
```

## Tạo video có QR

```python
from qr_video_tagger import QRVideoGenerator, LongVideoQRBuilder

gen = QRVideoGenerator(use_gpu="auto")  # không ghi file tạm, chạy song song an toàn
gen.process_video("in.mp4", "in_qr.mp4")
gen.process_folder("/videos/raw", "/videos/qr")

# Video bị cắt <base>_<part>.mp4 (>= 2 phần) -> ghép rồi nhúng QR; lỗi từng video nằm ở result["error"]
LongVideoQRBuilder(gen).run("/videos/raw", merged_dir="/videos/merged", output_dir="/videos/qr")
```

## Đọc QR

```python
from qr_video_tagger import QRVideoReader

reader = QRVideoReader()
first, last = reader.read_first_last(video_bytes)                 # (int|None, int|None)
frames_map = reader.read_all_from_bytes(video_bytes, frames_map)  # gán frame_qr_id
reader.read_fast("first_frame", "evidence.mp4")                  # int | None
reader.read_fast("all", "evidence.mp4")                          # list[int]
```

## Đổi vị trí/kích thước QR

Bước tạo và bước đọc dùng chung `QRLayout`, cần truyền cùng một layout cho cả hai:

```python
from qr_video_tagger import QRLayout
layout = QRLayout(qr_image_size=50, overlay_margin=10, crop_size=60, crop_offset_x=5, crop_offset_y=5)
QRVideoGenerator(layout=layout); QRVideoReader(layout=layout)
```

## Mapping với code cũ (`src/lib/qr_code`)

| Cũ | Mới |
|---|---|
| `test_qr.process_video / process_folder` | `QRVideoGenerator.process_video / process_folder` |
| `create_qr_video_long.create_qr_video_long` | `LongVideoQRBuilder.run` |
| `read_qr_code.read_qr_in_frame` | `QRVideoReader.read_frame` |
| `read_qr_code.read_all_qr_by_video_path` | `QRVideoReader.read_by_path` |
| `read_qr_code.read_qr_fast` | `QRVideoReader.read_fast` |
| `read_qr_code.read_qr_first_last` | `QRVideoReader.read_first_last` |
| `read_qr_code.read_all_qr_from_video_bytes` | `QRVideoReader.read_all_from_bytes` |
| `cap_frame_test.capture_frame / capture_multiple_frames` | `FrameCapture.capture / capture_many` |
| `src.utils.gpu_accel` | `FFmpegEncoder` |
