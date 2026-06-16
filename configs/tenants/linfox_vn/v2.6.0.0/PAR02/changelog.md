2026-03-28: Align PAR02 config across all cameras. Set `threshold` to 0.75 (down from 0.9 in previous version), `confirmation_time_seconds` to 3 (down from 7 in previous version), `movement_threshold_bbox_ratio` to 2 (down from 4 in previous version).
2026-01-30: New approach for PAR02 - Remove `violent_count`. Add `fps`, `confirmation_time_seconds`, `padding_time_seconds`, `movement_threshold_bbox_ratio`, `phone_detection_ratio`.
2025-11-25: Increase confidence threshold for usingphone classification model from 0.75 to 0.9.
