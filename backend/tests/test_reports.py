from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.database import Database  # noqa: E402
from app.notifications import PushResult  # noqa: E402
from app.reports import ReportScheduler  # noqa: E402


class FakeNotifier:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []

    async def send(self, title: str, body: str, *, group: str, **kwargs):
        self.messages.append((title, body, group))
        return PushResult(True, "ok")


def add_session(db: Database, start: datetime, minutes: int) -> None:
    session_id = db.start_session(start)
    db.end_session(session_id, start + timedelta(minutes=minutes), minutes >= 30)


def test_daily_report_uses_previous_complete_day_and_explicit_prefixes(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "daily.db")
        db.initialize()
        db.update_settings(
            {
                "bark_enabled": True,
                "daily_report_enabled": True,
                "daily_report_time": "00:00",
                "weekly_report_enabled": False,
                "daily_report_title": "{today_date} 推送 {yesterday_date} 小结",
                "daily_report_body": "昨日 {yesterday_total}/{yesterday_sessions}；今日 {today_total}/{today_sessions}",
            }
        )
        # 2026-08-30 12:00 与 2026-08-31 09:00（Asia/Shanghai）
        add_session(db, datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc), 60)
        add_session(db, datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc), 30)
        notifier = FakeNotifier()
        scheduler = ReportScheduler(db, notifier, "Asia/Shanghai")
        now = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
        await scheduler.check(now)
        assert notifier.messages == [("2026-08-31 推送 2026-08-30 小结", "昨日 1小时0分/1；今日 30分钟/1", "每日小结")]
        assert db.notification_exists("daily-v2", "2026-08-30") is True

    asyncio.run(scenario())


def test_weekly_report_renders_custom_placeholders(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "weekly.db")
        db.initialize()
        db.update_settings(
            {
                "bark_enabled": True,
                "daily_report_enabled": False,
                "weekly_report_enabled": True,
                "weekly_report_day": 7,
                "weekly_report_time": "00:00",
                "weekly_report_title": "{week_start}–{week_end}",
                "weekly_report_body": "累计 {week_total}，日均 {week_daily_average}，久坐 {week_sedentary}",
            }
        )
        add_session(db, datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc), 60)
        notifier = FakeNotifier()
        scheduler = ReportScheduler(db, notifier, "Asia/Shanghai")
        await scheduler.check(datetime(2026, 8, 30, 6, 0, tzinfo=timezone.utc))
        assert notifier.messages[0][0] == "2026-08-24–2026-08-30"
        assert "累计 1小时" in notifier.messages[0][1]
        assert "久坐 1" in notifier.messages[0][1]

    asyncio.run(scenario())
