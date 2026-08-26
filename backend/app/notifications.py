from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushResult:
    success: bool
    detail: str


class Notifier:
    def __init__(self, config: Config):
        self.config = config

    async def send(self, title: str, body: str, *, group: str = "松坐", use_bark: bool = True, use_webhook: bool = True) -> PushResult:
        results: list[str] = []
        success = False
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            if use_bark and self.config.bark_device_key:
                try:
                    response = await client.post(
                        f"{self.config.bark_server}/push",
                        json={"device_key": self.config.bark_device_key, "title": title, "body": body, "group": group, "icon": "https://api.day.app/assets/images/avatar.jpg"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    bark_ok = payload.get("code") == 200
                    success = success or bark_ok
                    results.append("Bark 已发送" if bark_ok else f"Bark 拒绝：{payload.get('message', '未知错误')}")
                except Exception as exc:  # noqa: BLE001 - notification failures must not stop monitoring
                    logger.warning("Bark push failed: %s", exc)
                    results.append(f"Bark 失败：{type(exc).__name__}")
            if use_webhook and self.config.generic_webhook_url:
                try:
                    response = await client.post(self.config.generic_webhook_url, json={"title": title, "body": body, "group": group})
                    response.raise_for_status()
                    success = True
                    results.append("Webhook 已发送")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Generic webhook failed: %s", exc)
                    results.append(f"Webhook 失败：{type(exc).__name__}")
        if not results:
            return PushResult(False, "未配置 Bark 密钥或通用 Webhook")
        return PushResult(success, "；".join(results))
