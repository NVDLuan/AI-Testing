"""Hand and pose keypoint extraction using MediaPipe.

Every frame always produces exactly 48 lines (keypoints):
    Indices  0-20: Left hand landmarks 0-20
    Indices 21-41: Right hand landmarks 0-20
    Indices 42-47: Pose landmarks 0-5

Output format (per line):
    Hand landmarks:  frame_index L|R landmark_id x y z
    Pose landmarks:  frame_index P landmark_id x y z

When a hand or pose is not detected, its keypoints are written as 0.000000
for all coordinates, ensuring a fixed 48-line structure per frame that maps
directly to the Triton input shape [15, 48, 3].

Pose landmark IDs (0-5) map to MediaPipe Pose indices:
    0=left_shoulder(11), 1=right_shoulder(12),
    2=left_elbow(13),    3=right_elbow(14),
    4=left_wrist(15),    5=right_wrist(16)
"""

from __future__ import annotations

import atexit
import os
import threading
import logging

import cv2
import mediapipe as mp

log = logging.getLogger(__name__)

# Upper-body pose landmark indices from MediaPipe Pose (33 total).
_UPPER_BODY_POSE_INDICES = [11, 12, 13, 14, 15, 16]

# Holistic model configuration. 
_MODEL_COMPLEXITY = 0
_MIN_DETECTION_CONFIDENCE = 0.3
_MIN_TRACKING_CONFIDENCE = 0.3

# Process-wide Holistic singleton. MediaPipe Holistic is not thread-safe and
# a single instance is too heavy to duplicate per camera on the Pi, so
# _HOLISTIC_LOCK intentionally serializes all extraction across cameras.
_HOLISTIC: "mp.solutions.holistic.Holistic | None" = None
_HOLISTIC_LOCK = threading.Lock()
_HOLISTIC_INIT_LOCK = threading.Lock()


def _get_holistic() -> "mp.solutions.holistic.Holistic":
    """Return the shared Holistic instance, creating it on first call."""
    global _HOLISTIC
    if _HOLISTIC is not None:
        return _HOLISTIC
    with _HOLISTIC_INIT_LOCK:
        if _HOLISTIC is None:
            log.info("Initializing shared MediaPipe Holistic (complexity=%d)", _MODEL_COMPLEXITY)
            _HOLISTIC = mp.solutions.holistic.Holistic(
                static_image_mode=False,
                model_complexity=_MODEL_COMPLEXITY,
                smooth_landmarks=True,
                min_detection_confidence=_MIN_DETECTION_CONFIDENCE,
                min_tracking_confidence=_MIN_TRACKING_CONFIDENCE,
            )
    return _HOLISTIC


def _close_holistic() -> None:
    """Release the shared Holistic on interpreter shutdown."""
    global _HOLISTIC
    if _HOLISTIC is not None:
        try:
            _HOLISTIC.close()
        except Exception:
            pass
        _HOLISTIC = None


atexit.register(_close_holistic)


class KeypointExtractor:
    """
    Extracts hand + upper-body pose landmarks from every frame of a video.
    Holds a reference to the process-wide Holistic singleton; instances are cheap and can be created per-call.
    """

    def extract_keypoints(self, video_path: str) -> str | None:
        """Extract hand + pose keypoints from a video and save to a .txt file.

        Returns the path to the generated .txt file, or None on failure.
        """
        if not os.path.isfile(video_path):
            log.error("Video file not found: %s", video_path)
            return None

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            log.error("Cannot open video: %s", video_path)
            return None

        base, _ = os.path.splitext(video_path)
        output_path = f"{base}_keypoints.txt"

        # reuse the shared Holistic
        holistic = _get_holistic()

        frame_idx = 0
        try:
            # Holistic is not thread-safe; the lock serializes concurrent
            # post-record callbacks from other cameras for the full video.
            with _HOLISTIC_LOCK, open(output_path, "w") as f:
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break

                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results = holistic.process(rgb)

                    # --- Left hand (indices 0-20) ---
                    left_landmarks = (
                        results.left_hand_landmarks.landmark
                        if results.left_hand_landmarks
                        else None
                    )
                    for lm_id in range(21):
                        if left_landmarks:
                            lm = left_landmarks[lm_id]
                            f.write(
                                f"{frame_idx} L {lm_id} "
                                f"{lm.x:.6f} {lm.y:.6f} {lm.z:.6f}\n"
                            )
                        else:
                            f.write(
                                f"{frame_idx} L {lm_id} "
                                f"0.000000 0.000000 0.000000\n"
                            )

                    # --- Right hand (indices 21-41) ---
                    right_landmarks = (
                        results.right_hand_landmarks.landmark
                        if results.right_hand_landmarks
                        else None
                    )
                    for lm_id in range(21):
                        if right_landmarks:
                            lm = right_landmarks[lm_id]
                            f.write(
                                f"{frame_idx} R {lm_id} "
                                f"{lm.x:.6f} {lm.y:.6f} {lm.z:.6f}\n"
                            )
                        else:
                            f.write(
                                f"{frame_idx} R {lm_id} "
                                f"0.000000 0.000000 0.000000\n"
                            )

                    # --- Pose landmarks (indices 42-47) ---
                    pose_landmarks = (
                        results.pose_landmarks.landmark
                        if results.pose_landmarks
                        else None
                    )
                    for out_id, mp_idx in enumerate(_UPPER_BODY_POSE_INDICES):
                        if pose_landmarks:
                            lm = pose_landmarks[mp_idx]
                            f.write(
                                f"{frame_idx} P {out_id} "
                                f"{lm.x:.6f} {lm.y:.6f} {lm.z:.6f}\n"
                            )
                        else:
                            f.write(
                                f"{frame_idx} P {out_id} "
                                f"0.000000 0.000000 0.000000\n"
                            )

                    frame_idx += 1
        except Exception as exc:
            log.error("Keypoint extraction failed for %s: %s", video_path, exc)
            cap.release()
            # do not close the shared Holistic here — atexit handles it.
            return None

        cap.release()
        # shared instance survives across videos; atexit handles teardown.

        log.info(
            "Extracted keypoints (hands+pose) from %d frames → %s",
            frame_idx,
            output_path,
        )
        return output_path