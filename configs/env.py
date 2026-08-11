"""Nạp biến môi trường từ file .env (không cần thư viện ngoài).

Quy tắc: mỗi dòng KEY=VALUE; bỏ qua dòng trống và dòng bắt đầu bằng '#'.
Giá trị có thể bọc trong nháy đơn/kép. KHÔNG ghi đè biến đã có sẵn trong môi
trường (env thật ưu tiên hơn file .env).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("ai_testing.env")


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        if path != ".env":  # người dùng chỉ định rõ (--env-file) -> báo để khỏi âm thầm thiếu secret
            log.warning("File env không tồn tại: %s", path)
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    log.info("Đã nạp biến môi trường từ %s", path)
