"""Cấu hình vị trí/kích thước QR dùng chung cho bước tạo và bước đọc.

Hai phía PHẢI khớp nhau:
- Tạo: QR resize về `qr_image_size` (50px), overlay tại `W - w - overlay_margin : overlay_margin`
  -> QR nằm ở x ∈ [W-60, W-10], y ∈ [10, 60]
- Đọc: crop vùng `crop_size` (60px) cách mép phải/trên `crop_offset_x/y` (5px)
  -> x ∈ [W-65, W-5], y ∈ [5, 65], bao trọn QR + 5px lề, rồi tô đen `border_size` px viền.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class QRLayout:
    # --- Tạo QR ---
    qr_image_size: int = 50
    qr_box_size: int = 2
    qr_border: int = 1
    overlay_margin: int = 10

    # --- Đọc QR ---
    crop_size: int = 60
    crop_offset_x: int = 5
    crop_offset_y: int = 5
    border_size: int = 4


DEFAULT_LAYOUT = QRLayout()
