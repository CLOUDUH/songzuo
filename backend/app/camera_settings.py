from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)


class CameraSettingsStore:
    """将摄像头凭据与分析数据库分开保存，并且永不通过 API 回传秘密。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self, base: Config) -> Config:
        if not self.path.is_file():
            return base
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return replace(
                base,
                camera_stream_url=str(payload.get("stream_url", base.camera_stream_url)),
                camera_username=str(payload.get("username", base.camera_username)),
                camera_password=str(payload.get("password", base.camera_password)),
                camera_auth_type=str(payload.get("auth_type", base.camera_auth_type)),
                camera_cookie=str(payload.get("cookie", base.camera_cookie)),
                camera_verify_tls=bool(payload.get("verify_tls", base.camera_verify_tls)),
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Unable to load camera settings: %s", exc)
            return base

    def merge(self, current: Config, payload: dict[str, Any]) -> Config:
        auth_type = str(payload["auth_type"])
        username = str(payload["username"])
        password = current.camera_password if payload.get("password") is None else str(payload["password"])
        cookie = current.camera_cookie if payload.get("cookie") is None else str(payload["cookie"])
        if auth_type == "none":
            username, password = "", ""
        return replace(
            current,
            camera_stream_url=str(payload["stream_url"]),
            camera_username=username,
            camera_password=password,
            camera_auth_type=auth_type,
            camera_cookie=cookie,
            camera_verify_tls=bool(payload["verify_tls"]),
        )

    def save(self, config: Config) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "stream_url": config.camera_stream_url,
                    "username": config.camera_username,
                    "password": config.camera_password,
                    "auth_type": config.camera_auth_type,
                    "cookie": config.camera_cookie,
                    "verify_tls": config.camera_verify_tls,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)

    @staticmethod
    def public(config: Config, *, online: bool, error: str) -> dict[str, Any]:
        return {
            "stream_url": config.camera_stream_url,
            "username": config.camera_username,
            "auth_type": config.camera_auth_type,
            "verify_tls": config.camera_verify_tls,
            "password_configured": bool(config.camera_password),
            "cookie_configured": bool(config.camera_cookie),
            "online": online,
            "error": error,
        }
