from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .camera import MjpegReader
from .database import Database
from .notifications import Notifier

logger = logging.getLogger(__name__)


class CameraHealthAlerts:
    """摄像头持续离线后提醒；恢复时只在先前确实提醒过的情况下通知。"""

    def __init__(self, db: Database, camera: MjpegReader, notifier: Notifier):
        self.db, self.camera, self.notifier = db, camera, notifier
        self.offline_since: datetime | None = None
        self.alert_sent = False
        self.last_attempt: datetime | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.check()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def check(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        snapshot = self.camera.snapshot()
        settings = self.db.settings()
        push_available = settings["bark_enabled"] or settings["webhook_enabled"]

        if snapshot.online:
            if self.alert_sent and settings["camera_recovery_alert_enabled"] and push_available:
                result = await self.notifier.send(
                    "摄像头已恢复在线",
                    "松坐已经重新收到摄像头画面，久坐监测已自动恢复。",
                    group="服务状态",
                    use_bark=settings["bark_enabled"],
                    use_webhook=settings["webhook_enabled"],
                )
                self.db.log_notification("camera-recovered", now.isoformat(timespec="minutes"), now, result.success, result.detail)
            self.offline_since = None
            self.alert_sent = False
            self.last_attempt = None
            return

        self.offline_since = self.offline_since or now
        if not settings["camera_offline_alert_enabled"] or not push_available:
            return
        threshold = timedelta(minutes=int(settings["camera_offline_minutes"]))
        if now - self.offline_since < threshold or self.alert_sent:
            return
        if self.last_attempt and now - self.last_attempt < timedelta(minutes=5):
            return

        self.last_attempt = now
        reason = snapshot.error or "未收到摄像头画面"
        result = await self.notifier.send(
            "摄像头离线提醒",
            f"松坐已连续 {settings['camera_offline_minutes']} 分钟无法获取画面。原因：{reason}。当前无法确认座位状态，服务会继续自动重连。",
            group="服务状态",
            use_bark=settings["bark_enabled"],
            use_webhook=settings["webhook_enabled"],
        )
        self.db.log_notification("camera-offline", now.isoformat(timespec="minutes"), now, result.success, result.detail)
        self.alert_sent = result.success
