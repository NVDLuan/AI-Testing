"""Tiện ích chụp frame theo id để kiểm tra thủ công."""
from __future__ import annotations

import os
import tempfile
from typing import Dict, List, Union

import cv2
import numpy as np


class FrameCapture:
    def __init__(self, default_output_dir: str = "/tmp"):
        self.default_output_dir = default_output_dir

    def capture(self, video_input: Union[str, bytes], frame_id: int, output_path: str | None = None) -> np.ndarray:
        """Chụp frame tại `frame_id` (bắt đầu từ 0) từ path hoặc bytes, lưu PNG."""
        temp_video = None
        if isinstance(video_input, bytes):
            fd, temp_video = tempfile.mkstemp(suffix=".mp4")
            with os.fdopen(fd, "wb") as f:
                f.write(video_input)
            video_path = temp_video
        else:
            video_path = video_input

        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise ValueError(f"Không mở được video: {video_path}")

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_id < 0 or frame_id >= total_frames:
                cap.release()
                raise ValueError(f"Frame ID {frame_id} không hợp lệ. Video có {total_frames} frames (0-{total_frames - 1})")

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_id)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                raise ValueError(f"Không đọc được frame {frame_id}")

            if output_path is None:
                os.makedirs(self.default_output_dir, exist_ok=True)
                output_path = os.path.join(self.default_output_dir, f"frame_{frame_id}.png")
            else:
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            cv2.imwrite(output_path, frame)
            print(f"Đã lưu frame {frame_id} tại: {output_path}")
            return frame
        finally:
            if temp_video and os.path.exists(temp_video):
                os.remove(temp_video)

    def capture_many(self, video_input: Union[str, bytes], frame_ids: List[int],
                     output_dir: str = "/tmp/frames") -> Dict[int, np.ndarray]:
        os.makedirs(output_dir, exist_ok=True)
        frames = {}
        for frame_id in frame_ids:
            frames[frame_id] = self.capture(video_input, frame_id, os.path.join(output_dir, f"frame_{frame_id}.png"))
        print(f"Đã lưu {len(frames)} frames tại: {output_dir}")
        return frames
