from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.database import Database  # noqa: E402
from app.detector import Detection  # noqa: E402
from app.monitor import Monitor  # noqa: E402
from app.notifications import PushResult  # noqa: E402
from app.stats import summarize  # noqa: E402


class FakeCamera:
    def snapshot(self):
        return type("Snapshot", (), {"online": True, "error": ""})()


class FakeDetector:
    name = "fake"


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send(self, title: str, body: str, **kwargs):
        self.messages.append((title, body))
        return PushResult(True, "ok")


async def feed(monitor, detection, now, settings):
    # Regular observations between event boundaries; real gaps are tested separately.
    previous = monitor.last_check
    while previous and (now - previous).total_seconds() > 3:
        previous += timedelta(seconds=3)
        await monitor.process(monitor.test_previous_detection, previous, settings)
    await monitor.process(detection, now, settings)
    monitor.test_previous_detection = detection


def create_monitor(tmp_path: Path, **updates):
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.update_settings({"sample_interval_seconds": 3, "leave_confirm_seconds": 9, "merge_gap_seconds": 120, **updates})
    notifier = FakeNotifier()
    monitor = Monitor(db, FakeCamera(), FakeDetector(), notifier)  # type: ignore[arg-type]
    return db, notifier, monitor, db.settings()


def test_monitor_sends_one_sedentary_reminder(tmp_path: Path) -> None:
    async def scenario() -> None:
        db, notifier, monitor, settings = create_monitor(tmp_path, sedentary_minutes=15, bark_enabled=True)
        start = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)
        await feed(monitor, Detection(True, 0.9), start, settings)
        await feed(monitor, Detection(True, 0.9), start + timedelta(seconds=7), settings)
        await feed(monitor, Detection(True, 0.95), start + timedelta(minutes=16), settings)
        await feed(monitor, Detection(True, 0.95), start + timedelta(minutes=17), settings)
        assert len(notifier.messages) == 1
        assert db.active_session()["is_sedentary"] == 1

    asyncio.run(scenario())


def test_monitor_repeats_reminders_at_seated_time_interval(tmp_path: Path) -> None:
    async def scenario() -> None:
        db, notifier, monitor, settings = create_monitor(
            tmp_path,
            sedentary_minutes=15,
            bark_enabled=True,
            repeat_reminder_enabled=True,
            repeat_reminder_minutes=30,
            repeat_reminder_title="追加提醒 {repeat_count}",
            repeat_reminder_body="已坐 {duration}，间隔 {interval}",
        )
        start = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)
        await feed(monitor, Detection(True, 0.9), start, settings)
        await feed(monitor, Detection(True, 0.9), start + timedelta(seconds=7), settings)
        await feed(monitor, Detection(True, 0.95), start + timedelta(minutes=16), settings)
        assert len(notifier.messages) == 1

        # 同一有效会话按实际坐姿时间重复提醒；服务重启另测。
        restarted = monitor
        await feed(restarted, Detection(True, 0.95), start + timedelta(minutes=45), settings)
        assert len(notifier.messages) == 1
        await feed(restarted, Detection(True, 0.95), start + timedelta(minutes=46), settings)
        await feed(restarted, Detection(True, 0.95), start + timedelta(minutes=76), settings)

        assert notifier.messages == [
            ("该起身活动啦", "你已经连续坐了 15分钟，走动几分钟吧。"),
            ("追加提醒 1", "已坐 45分钟，间隔 30分钟"),
            ("追加提醒 2", "已坐 1小时15分，间隔 30分钟"),
        ]
        active = db.active_session()
        assert active is not None
        assert active["reminder_count"] == 3
        assert 75 * 60 <= active["last_reminder_elapsed_seconds"] <= 75 * 60 + 6

    asyncio.run(scenario())


def test_short_break_is_merged_but_excluded_from_seated_time(tmp_path: Path) -> None:
    async def scenario() -> None:
        db, _, monitor, settings = create_monitor(tmp_path)
        start = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)
        await feed(monitor, Detection(True, 0.9), start, settings)
        await feed(monitor, Detection(True, 0.9), start + timedelta(seconds=7), settings)

        break_start = start + timedelta(minutes=10)
        await feed(monitor, Detection(False, 0), break_start, settings)
        await feed(monitor, Detection(False, 0), break_start + timedelta(seconds=10), settings)
        assert monitor.occupied is False
        assert db.active_session() is not None

        return_at = break_start + timedelta(seconds=60)
        await feed(monitor, Detection(True, 0.9), return_at, settings)
        await feed(monitor, Detection(True, 0.9), return_at + timedelta(seconds=7), settings)
        assert monitor.occupied is True
        assert monitor.away_seconds == 60
        assert monitor.seated_elapsed(start + timedelta(minutes=12)) == 11 * 60

        final_leave = start + timedelta(minutes=20)
        await feed(monitor, Detection(False, 0), final_leave, settings)
        await feed(monitor, Detection(False, 0), final_leave + timedelta(seconds=121), settings)
        row = db.overlapping_sessions(start, start + timedelta(hours=1), start + timedelta(hours=1))[0]
        assert row["ended_at"] == final_leave.isoformat()
        assert row["away_seconds"] == 60
        assert 1139 <= row["duration_seconds"] <= 1140
        summary = summarize(db, start, start + timedelta(hours=1), start + timedelta(hours=1))
        assert 1139 <= summary["total_seconds"] <= 1140

    asyncio.run(scenario())
