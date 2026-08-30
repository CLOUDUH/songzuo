from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    database_path: Path = Path(os.getenv("DATABASE_PATH", "/app/data/songzuo.db"))
    camera_settings_path: Path = Path(os.getenv("CAMERA_SETTINGS_PATH", "/app/data/camera-settings.json"))
    notification_settings_path: Path = Path(os.getenv("NOTIFICATION_SETTINGS_PATH", "/app/data/notification-settings.json"))
    static_dir: Path = Path(os.getenv("STATIC_DIR", "/app/static"))
    camera_stream_url: str = os.getenv("CAMERA_STREAM_URL", "http://192.168.1.80:2345/")
    camera_username: str = os.getenv("CAMERA_USERNAME", "")
    camera_password: str = os.getenv("CAMERA_PASSWORD", "")
    camera_auth_type: str = os.getenv("CAMERA_AUTH_TYPE", "basic").lower()
    camera_cookie: str = os.getenv("CAMERA_COOKIE", "")
    camera_verify_tls: bool = os.getenv("CAMERA_VERIFY_TLS", "true").lower() == "true"
    detector_mode: str = os.getenv("DETECTOR_MODE", "auto").lower()
    model_path: Path = Path(os.getenv("MODEL_PATH", "/app/models/yolov8n.onnx"))
    face_model_path: Path = Path(os.getenv("FACE_MODEL_PATH", "/app/assets/face_detection_yunet_2023mar.onnx"))
    detector_input_size: int = _int("DETECTOR_INPUT_SIZE", 320)
    opencv_threads: int = _int("OPENCV_THREADS", 2)
    timezone: str = os.getenv("TZ", "Asia/Shanghai")
    bark_server: str = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
    bark_device_key: str = os.getenv("BARK_DEVICE_KEY", "")
    generic_webhook_url: str = os.getenv("GENERIC_WEBHOOK_URL", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()


config = Config()
