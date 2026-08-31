from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .database import Database
from .notifications import Notifier
from .stats import boundaries, format_duration, summarize


def render_template(template: str, values: dict[str, str | int]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered


def summary_values(prefix: str, data: dict[str, int]) -> dict[str, str | int]:
    return {
        f"{prefix}_total": format_duration(data["total_seconds"]),
        f"{prefix}_sessions": data["session_count"],
        f"{prefix}_sedentary": data["sedentary_count"],
        f"{prefix}_longest": format_duration(data["longest_seconds"]),
    }


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
            today_start_local = datetime.combine(local.date(), time.min, tzinfo=self.tz)
            yesterday_start_local = today_start_local - timedelta(days=1)
            yesterday_key = yesterday_start_local.date().isoformat()
            if not self.db.notification_exists("daily-v2", yesterday_key):
                yesterday = summarize(
                    self.db,
                    yesterday_start_local.astimezone(timezone.utc),
                    today_start_local.astimezone(timezone.utc),
                    now,
                )
                today = summarize(self.db, today_start_local.astimezone(timezone.utc), now, now)
                values: dict[str, str | int] = {
                    "today_date": local.date().isoformat(),
                    "yesterday_date": yesterday_start_local.date().isoformat(),
                    **summary_values("today", today),
                    **summary_values("yesterday", yesterday),
                }
                title = render_template(str(settings["daily_report_title"]), values)
                body = render_template(str(settings["daily_report_body"]), values)
                result = await self.notifier.send(
                    title,
                    body,
                    group="每日小结",
                    use_bark=settings["bark_enabled"],
                    use_webhook=settings["webhook_enabled"],
                )
                self.db.log_notification("daily-v2", yesterday_key, now, result.success, result.detail)

        if settings["weekly_report_enabled"] and local.isoweekday() == settings["weekly_report_day"] and current_hhmm >= settings["weekly_report_time"]:
            week_start_date = local.date() - timedelta(days=local.weekday())
            week_key = week_start_date.isoformat()
            if not self.db.notification_exists("weekly", week_key):
                start, end = boundaries(now, self.tz)["week"]
                data = summarize(self.db, start, min(end, now), now)
                elapsed_days = max(1, (local.date() - week_start_date).days + 1)
                values = {
                    "week_start": week_start_date.isoformat(),
                    "week_end": local.date().isoformat(),
                    "week_total": format_duration(data["total_seconds"]),
                    "week_daily_average": format_duration(int(data["total_seconds"] / elapsed_days)),
                    "week_sessions": data["session_count"],
                    "week_sedentary": data["sedentary_count"],
                    "week_longest": format_duration(data["longest_seconds"]),
                }
                title = render_template(str(settings["weekly_report_title"]), values)
                body = render_template(str(settings["weekly_report_body"]), values)
                result = await self.notifier.send(
                    title,
                    body,
                    group="每周报告",
                    use_bark=settings["bark_enabled"],
                    use_webhook=settings["webhook_enabled"],
                )
                self.db.log_notification("weekly", week_key, now, result.success, result.detail)
