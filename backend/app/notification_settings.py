from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Config

logger = logging.getLogger(__name__)


class NotificationSettingsStore:
    """单独保存推送端点和密钥，API 只返回脱敏状态。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self, base: Config) -> Config:
        if not self.path.is_file():
            return base
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return replace(
                base,
                bark_server=str(payload.get("bark_server", base.bark_server)).rstrip("/"),
                bark_device_key=str(payload.get("bark_device_key", base.bark_device_key)),
                generic_webhook_url=str(payload.get("generic_webhook_url", base.generic_webhook_url)),
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Unable to load notification settings: %s", exc)
            return base

    def merge(self, current: Config, payload: dict[str, Any]) -> Config:
        server = str(payload["bark_server"]).rstrip("/")
        key_value = payload.get("bark_device_key")
        key = current.bark_device_key if key_value is None else str(key_value).strip()
        if key.startswith(("http://", "https://")):
            parsed = urlparse(key)
            parts = [part for part in parsed.path.split("/") if part]
            if parts:
                key = parts[0]
                server = f"{parsed.scheme}://{parsed.netloc}"
        webhook_value = payload.get("generic_webhook_url")
        webhook = current.generic_webhook_url if webhook_value is None else str(webhook_value).strip()
        return replace(current, bark_server=server, bark_device_key=key, generic_webhook_url=webhook)

    def save(self, config: Config) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "bark_server": config.bark_server,
                    "bark_device_key": config.bark_device_key,
                    "generic_webhook_url": config.generic_webhook_url,
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
    def public(config: Config) -> dict[str, Any]:
        return {
            "bark_server": config.bark_server,
            "bark_device_key_configured": bool(config.bark_device_key),
            "generic_webhook_configured": bool(config.generic_webhook_url),
        }
