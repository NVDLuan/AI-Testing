"""Vẽ zone + overlay config của 1 camera (canvas trống hoặc frame đầu 1 video thật) ->
lưu ra file để xem bằng mắt config đã khai zone (cam_zones/no_walking_zones/...) hay
chưa, và khớp với hiện trường thật không.

`ZoneDrawer.draw()` (adapters/draw_zone.py) tự vẽ cả zone lẫn overlay text các field
config còn lại (góc trên-trái) -> file này chỉ lo chuẩn bị ảnh nền (canvas trống hay
frame video thật) rồi gọi `draw()`.

Chạy mặc định (2 mẫu sẵn, canvas trống: 1 config CHẮC CHẮN có zone để sanity-check việc
vẽ, 1 config karter_school đang dùng thật để kiểm tra):
    python -m tests.test_draw_zone

Vẽ lên canvas trống với config bất kỳ:
    python -m tests.test_draw_zone --config <config.json> --camera <camera_id> [--out out.png]

Vẽ lên frame đầu của 1 video thật:
    python -m tests.test_draw_zone --config <config.json> --camera <camera_id> --video <video.mp4> [--out out.png]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from adapters.draw_zone import ZoneDrawer

OUT_DIR = Path("reports/draw_zone_preview")

# Config chắc chắn có no_walking_zones (pixel coords) -> sanity-check việc vẽ hoạt động.
SAMPLE_WITH_ZONE = ("configs/tenants/linfox_vn/v2.8.1.0/HM05/HM05_config.json", "linfox_DCBD-192_168_253_137")
# Config karter_school đang trỏ thật trong configs/config.yaml (version 3.0.0.1),
# rule UNDER RATIO[CNT09] -> kiểm tra xem đã khai zone chưa.
SAMPLE_KARTER = ("configs/tenants/karter_school/v3.0.0.1/CNT09/CNT09_config.json", "ksr_02")


def load_first_frame(video_path: str) -> np.ndarray:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Không mở được video: {video_path}")
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        raise RuntimeError(f"Không đọc được frame đầu của video: {video_path}")
    return frame


def load_camera_config(config_path: str, camera_id: str) -> dict:
    data = json.loads(Path(config_path).read_text())
    if camera_id not in data:
        raise KeyError(f"{config_path}: không có camera_id {camera_id!r} (có: {list(data)})")
    return data[camera_id]


def draw_camera_config(
    config_path: str,
    camera_id: str,
    out_path: str,
    frame: np.ndarray | None = None,
) -> list[tuple[str, int, str]]:
    """Vẽ zone + overlay config của 1 camera lên `frame` (hoặc canvas trống nếu không
    truyền), lưu ra `out_path`.

    Trả về list (field_name, số zone, zone_type) tìm thấy trong config -> biết ngay
    config đã khai zone hay chưa mà không cần mở ảnh lên xem.
    """
    cam_cfg = load_camera_config(config_path, camera_id)

    if frame is None:
        width, height = cam_cfg.get("cam_resolution") or [1280, 720]
        frame = np.full((height, width, 3), 40, dtype=np.uint8)  # nền xám đậm, dễ thấy zone
    else:
        frame = frame.copy()

    drawer = ZoneDrawer()
    zone_fields = drawer._detect_all_zone_fields(cam_cfg)
    result = drawer.draw(frame, cam_cfg)  # vẽ zone + overlay config, cả 2 đều trong draw()

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out_path, result)

    return [(name, len(zones), zt) for name, zones, zt in zone_fields]


def _run(config_path: str, camera_id: str, out_path: str, video_path: str | None = None) -> None:
    frame = load_first_frame(video_path) if video_path else None
    fields = draw_camera_config(config_path, camera_id, out_path, frame=frame)
    if fields:
        for name, count, zt in fields:
            print(f"  - {name}: {count} zone(s), type={zt}")
    else:
        print(f"  !! KHÔNG tìm thấy field zone nào trong config camera {camera_id!r}")
    print(f"  -> ảnh: {out_path}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="Đường dẫn <CODE>_config.json")
    p.add_argument("--camera", help="camera_id trong config")
    p.add_argument("--video", default="", help="Video -> lấy frame đầu làm nền thay vì canvas trống")
    p.add_argument("--out", default="", help="File ảnh output (mặc định reports/draw_zone_preview/<camera>.png)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.config and args.camera:
        out = args.out or str(OUT_DIR / f"{Path(args.config).stem}_{args.camera}.png")
        _run(args.config, args.camera, out, video_path=args.video or None)
    else:
        print(f"[sanity check] {SAMPLE_WITH_ZONE[0]} | {SAMPLE_WITH_ZONE[1]}")
        _run(*SAMPLE_WITH_ZONE, str(OUT_DIR / "linfox_hm05_137.png"))

        print(f"\n[karter_school] {SAMPLE_KARTER[0]} | {SAMPLE_KARTER[1]}")
        _run(*SAMPLE_KARTER, str(OUT_DIR / "karter_cnt09_ksr02.png"))
