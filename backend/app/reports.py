from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .database import Database
from .notifications import Notifier
from .stats import boundaries, format_duration, summarize


class ReportScheduler:
    def __init__(self, db: Database, notifier: Notifier, timezone_name: str):
        self.db, self.notifier, self.tz = db, notifier, ZoneInfo(timezone_name)
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.check()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def check(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(self.tz)
        settings = self.db.settings()
        if not settings["bark_enabled"] and not settings["webhook_enabled"]:
            return
        current_hhmm = local.strftime("%H:%M")
        if settings["daily_report_enabled"] and current_hhmm >= settings["daily_report_time"]:
            key = local.date().isoformat()
            if not self.db.notification_exists("daily", key):
                start, end = boundaries(now, self.tz)["day"]
                data = summarize(self.db, start, min(end, now), now)
                body = f"今日共坐下 {data['session_count']} 次，累计 {format_duration(data['total_seconds'])}，久坐 {data['sedentary_count']} 次。"
                result = await self.notifier.send("今日久坐小结", body, group="每日小结", use_bark=settings["bark_enabled"], use_webhook=settings["webhook_enabled"])
                self.db.log_notification("daily", key, now, result.success, result.detail)
        if settings["weekly_report_enabled"] and local.isoweekday() == settings["weekly_report_day"] and current_hhmm >= settings["weekly_report_time"]:
            week_start = local.date() - timedelta(days=local.weekday())
            key = week_start.isoformat()
            if not self.db.notification_exists("weekly", key):
                start, end = boundaries(now, self.tz)["week"]
                data = summarize(self.db, start, min(end, now), now)
                body = f"本周累计坐姿 {format_duration(data['total_seconds'])}，共 {data['session_count']} 次，久坐 {data['sedentary_count']} 次。"
                result = await self.notifier.send("本周久坐报告", body, group="每周报告", use_bark=settings["bark_enabled"], use_webhook=settings["webhook_enabled"])
                self.db.log_notification("weekly", key, now, result.success, result.detail)
