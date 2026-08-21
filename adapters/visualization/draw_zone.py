"""ZoneDrawer — vẽ các zone (polygon/line/points) từ camera config lên 1 frame ảnh.

File độc lập, chỉ phụ thuộc cv2 + numpy, có thể copy sang project khác dùng ngay.

Usage:
    from draw_zone import ZoneDrawer

    frame = ZoneDrawer().draw(frame, config)
"""
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# Bảng màu mềm cho nhiều zone field khác nhau (tránh đỏ gắt)
ZONE_COLOR_PALETTE = [
    (160, 200, 120),   # sage green
    (200, 180, 100),   # dusty blue
    (140, 190, 160),   # mint
    (190, 170, 200),   # lavender
    (170, 210, 210),   # pale cyan
    (180, 200, 140),   # olive
]


class VisualizationConfig:
    """Hằng số cấu hình cho việc vẽ zone."""
    ZONE_TRANSPARENCY = 0.22
    ZONE_BORDER_THICKNESS = 1
    LABEL_FONT_SIZE = 0.42
    LABEL_THICKNESS = 1
    LABEL_PADDING = 4
    LABEL_LEADER_OFFSET = 12
    LABEL_LEADER_THICKNESS = 1
    LABEL_BG_ALPHA = 0.75

    # Overlay thông tin config (text góc trên-trái)
    TEXT_FONT_SIZE = 0.42
    TEXT_LINE_HEIGHT = 20
    TEXT_Y_OFFSET = 28
    TEXT_X_OFFSET = 16
    TEXT_OUTLINE_COLOR = (0, 0, 0)
    TEXT_OUTLINE_THICKNESS = 2
    TEXT_COLOR = (255, 255, 255)
    TEXT_THICKNESS = 1


# Field config không vẽ được dạng zone (skip mặc định khi không có zone field nào khác)
DEFAULT_SKIP_FIELDS = ['no_walking_zones', 'cam_zones', 'intersection']


class ZoneDrawer:
    """Vẽ các zone (polygon/line/points) được khai báo trong camera config lên 1 frame."""

    def __init__(self, config: Optional[VisualizationConfig] = None):
        self.cfg = config or VisualizationConfig()

    # ---------- Public API ----------

    def draw(self, frame: np.ndarray, config: Dict) -> np.ndarray:
        """Vẽ toàn bộ zone field tìm thấy trong `config` lên `frame`, kèm overlay các
        thông tin config còn lại (field không phải zone) ở góc trên-trái, rồi trả về
        frame đã vẽ."""
        zone_fields = self._detect_all_zone_fields(config)

        if zone_fields:
            frame_h, frame_w = frame.shape[:2]
            all_zones = [zone for _, zones, _ in zone_fields for zone in zones]
            use_normalized = self._should_use_normalized_coords(config, all_zones)

            for field_idx, (field_name, zones, zone_type) in enumerate(zone_fields):
                color = ZONE_COLOR_PALETTE[field_idx % len(ZONE_COLOR_PALETTE)]
                for i, zone in enumerate(zones):
                    label = self._get_zone_label(field_name, i, len(zones))
                    pixel_zone = (
                        self._scale_to_pixels(zone, frame_w, frame_h)
                        if use_normalized
                        else zone
                    )
                    frame = self._draw_zone(frame, pixel_zone, label, zone_type, color)

        drawn_zone_keys = [name for name, _, _ in zone_fields]
        info_lines = self._build_config_info_lines(
            config, skip_fields=drawn_zone_keys or DEFAULT_SKIP_FIELDS
        )
        self._draw_config_overlay(frame, info_lines)

        return frame

    # ---------- Zone detection từ config ----------

    @staticmethod
    def _extract_polygons(value: object) -> List[List]:
        """Chuẩn hoá giá trị config zone về dạng list các polygon."""
        def is_point(p):
            return isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p)

        def is_polygon(p):
            return isinstance(p, list) and len(p) >= 2 and all(is_point(pt) for pt in p)

        if not value or not isinstance(value, list):
            return []
        if is_polygon(value):
            return [value]
        if all(is_polygon(item) for item in value):
            return value
        return []

    def _detect_all_zone_fields(self, config: Dict) -> List[Tuple[str, List, str]]:
        """Tìm tất cả field zone trong config. Trả về list (field_name, zones, zone_type)."""
        results: List[Tuple[str, List, str]] = []
        ordered_keys = ['image_points', 'alcohol_test_area'] + [
            k for k in config if k not in ('image_points', 'alcohol_test_area')
        ]

        for field in ordered_keys:
            value = config.get(field)
            if not value:
                continue

            if field == 'alcohol_test_area' and isinstance(value, dict):
                zones = [z for z in (value.get('start_line'), value.get('end_line')) if z]
                if zones:
                    results.append((field, zones, 'line'))
                continue

            if field == 'image_points':
                polygons = self._extract_polygons(value)
                if polygons:
                    results.append((field, polygons, 'points'))
                continue

            polygons = self._extract_polygons(value)
            if polygons:
                results.append((field, polygons, 'polygon'))

        return results

    @staticmethod
    def _should_use_normalized_coords(config: Dict, zones: List[List]) -> bool:
        """Toạ độ có phải dạng chuẩn hoá (0..1) hay không (để scale ra pixel)."""
        if 'is_normalized' in config:
            return bool(config['is_normalized'])
        for zone in zones:
            for x, y in zone:
                if abs(x) > 3 or abs(y) > 3:
                    return False
        return bool(zones)

    @staticmethod
    def _scale_to_pixels(zone: List, width: int, height: int) -> List[List[int]]:
        return [[int(round(x * width)), int(round(y * height))] for x, y in zone]

    @staticmethod
    def _get_zone_label(field_name: str, index: int, total: int) -> str:
        labels = {
            'no_walking_zones': 'NO WALKING ZONE',
            'cam_zones': 'CAM ZONE',
            'image_points': 'IMAGE POINTS',
            'intersection': 'INTERSECTION',
            'alcohol_test_area': 'ALCOHOL TEST LINE',
        }
        base = labels.get(field_name, field_name.replace('_', ' ').upper())
        return f"{base} #{index + 1}" if total > 1 else base

    # ---------- Vẽ ----------

    @staticmethod
    def _polygon_anchor(points: np.ndarray) -> Tuple[int, int]:
        idx = int(np.argmin(points[:, 1]))
        return int(points[idx, 0]), int(points[idx, 1])

    def _draw_zone(self, frame, zone, label, zone_type, color):
        cfg = self.cfg
        points = np.array(zone, dtype=np.int32)

        if zone_type == 'line' and len(points) == 2:
            cv2.line(frame, tuple(points[0]), tuple(points[1]), color, cfg.ZONE_BORDER_THICKNESS)
            anchor = (
                int((points[0][0] + points[1][0]) / 2),
                int((points[0][1] + points[1][1]) / 2),
            )
        elif zone_type == 'points':
            for i, point in enumerate(points):
                cv2.circle(frame, tuple(point), 5, color, -1)
                cv2.putText(frame, f"P{i + 1}", (point[0] + 8, point[1] - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, cfg.LABEL_FONT_SIZE, color,
                            cfg.LABEL_THICKNESS, cv2.LINE_AA)
            cv2.polylines(frame, [points], isClosed=True, color=color, thickness=cfg.ZONE_BORDER_THICKNESS)
            anchor = self._polygon_anchor(points)
        else:  # 'polygon' hoặc line có >2 điểm
            closed = zone_type != 'line'
            if zone_type == 'polygon':
                overlay = frame.copy()
                cv2.fillPoly(overlay, [points], color=color)
                cv2.addWeighted(overlay, cfg.ZONE_TRANSPARENCY, frame, 1 - cfg.ZONE_TRANSPARENCY, 0, frame)
            cv2.polylines(frame, [points], isClosed=closed, color=color, thickness=cfg.ZONE_BORDER_THICKNESS)
            anchor = self._polygon_anchor(points)

        self._draw_label_outside(frame, anchor, label, color)
        return frame

    def _draw_label_outside(self, frame, anchor, label, color):
        """Vẽ label bên ngoài zone kèm đường leader line, kiểu nhãn bounding box."""
        cfg = self.cfg
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        ax, ay = anchor
        (tw, th), baseline = cv2.getTextSize(label, font, cfg.LABEL_FONT_SIZE, cfg.LABEL_THICKNESS)
        pad, offset = cfg.LABEL_PADDING, cfg.LABEL_LEADER_OFFSET

        candidates = [
            (ax - tw // 2, ay - offset),
            (ax - tw // 2, ay + offset + th),
            (ax - tw - offset, ay - th // 2),
            (ax + offset, ay - th // 2),
        ]
        tx, ty = candidates[0]
        for cx, cy in candidates:
            if cx - pad >= 0 and cy - th - pad >= 0 and cx + tw + pad < w and cy + baseline + pad < h:
                tx, ty = cx, cy
                break
        tx = max(pad, min(tx, w - tw - pad))
        ty = max(th + pad, min(ty, h - baseline - pad))

        bg_left, bg_top = tx - pad, ty - th - pad
        bg_right, bg_bottom = tx + tw + pad, ty + baseline + pad
        label_cx, label_cy = tx + tw // 2, (bg_top + bg_bottom) // 2
        if bg_bottom <= ay:
            leader_end = (label_cx, bg_bottom)
        elif bg_top >= ay:
            leader_end = (label_cx, bg_top)
        elif bg_right <= ax:
            leader_end = (bg_right, label_cy)
        else:
            leader_end = (bg_left, label_cy)

        cv2.line(frame, anchor, leader_end, color, cfg.LABEL_LEADER_THICKNESS, cv2.LINE_AA)

        overlay = frame.copy()
        cv2.rectangle(overlay, (bg_left, bg_top), (bg_right, bg_bottom), color, -1)
        cv2.addWeighted(overlay, cfg.LABEL_BG_ALPHA, frame, 1 - cfg.LABEL_BG_ALPHA, 0, frame)
        cv2.rectangle(frame, (bg_left, bg_top), (bg_right, bg_bottom), color, 1)
        cv2.putText(frame, label, (tx, ty), font, cfg.LABEL_FONT_SIZE, (255, 255, 255),
                    cfg.LABEL_THICKNESS, cv2.LINE_AA)

    # ---------- Overlay thông tin config ----------

    @staticmethod
    def _format_config_value(value) -> str:
        if isinstance(value, list):
            if len(value) > 0 and isinstance(value[0], list):
                return f"[{len(value)} items]"
            if len(value) <= 3:
                return ', '.join(str(v) for v in value)
            return f"{', '.join(str(v) for v in value[:3])}..."
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    def _build_config_info_lines(self, config: Dict, skip_fields: List[str]) -> List[str]:
        """Dựng danh sách dòng text hiển thị các field config KHÔNG thuộc `skip_fields`."""
        lines = []
        for key, value in config.items():
            if key in skip_fields:
                continue
            full_name = key.replace('_', ' ').title()
            lines.append(f"{full_name}: {self._format_config_value(value)}")
        return lines

    def _draw_text_with_outline(self, frame: np.ndarray, text: str, position: Tuple[int, int]) -> None:
        cfg = self.cfg
        cv2.putText(frame, text, position, cv2.FONT_HERSHEY_SIMPLEX, cfg.TEXT_FONT_SIZE,
                    cfg.TEXT_OUTLINE_COLOR, cfg.TEXT_OUTLINE_THICKNESS, cv2.LINE_AA)
        cv2.putText(frame, text, position, cv2.FONT_HERSHEY_SIMPLEX, cfg.TEXT_FONT_SIZE,
                    cfg.TEXT_COLOR, cfg.TEXT_THICKNESS, cv2.LINE_AA)

    def _draw_config_overlay(self, frame: np.ndarray, info_lines: List[str]) -> None:
        cfg = self.cfg
        for i, line in enumerate(info_lines):
            x_pos = cfg.TEXT_X_OFFSET
            y_pos = cfg.TEXT_Y_OFFSET + i * cfg.TEXT_LINE_HEIGHT
            self._draw_text_with_outline(frame, line, (x_pos, y_pos))