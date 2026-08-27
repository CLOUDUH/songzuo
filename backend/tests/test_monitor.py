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
        await monitor.process(Detection(True, 0.9), start, settings)
        await monitor.process(Detection(True, 0.9), start + timedelta(seconds=7), settings)
        await monitor.process(Detection(True, 0.95), start + timedelta(minutes=16), settings)
        await monitor.process(Detection(True, 0.95), start + timedelta(minutes=17), settings)
        assert len(notifier.messages) == 1
        assert db.active_session()["is_sedentary"] == 1

    asyncio.run(scenario())


def test_short_break_is_merged_but_excluded_from_seated_time(tmp_path: Path) -> None:
    async def scenario() -> None:
        db, _, monitor, settings = create_monitor(tmp_path)
        start = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)
        await monitor.process(Detection(True, 0.9), start, settings)
        await monitor.process(Detection(True, 0.9), start + timedelta(seconds=7), settings)

        break_start = start + timedelta(minutes=10)
        await monitor.process(Detection(False, 0), break_start, settings)
        await monitor.process(Detection(False, 0), break_start + timedelta(seconds=10), settings)
        assert monitor.occupied is False
        assert db.active_session() is not None

        return_at = break_start + timedelta(seconds=60)
        await monitor.process(Detection(True, 0.9), return_at, settings)
        await monitor.process(Detection(True, 0.9), return_at + timedelta(seconds=7), settings)
        assert monitor.occupied is True
        assert monitor.away_seconds == 60
        assert monitor.seated_elapsed(start + timedelta(minutes=12)) == 11 * 60

        final_leave = start + timedelta(minutes=20)
        await monitor.process(Detection(False, 0), final_leave, settings)
        await monitor.process(Detection(False, 0), final_leave + timedelta(seconds=121), settings)
        row = db.overlapping_sessions(start, start + timedelta(hours=1), start + timedelta(hours=1))[0]
        assert row["ended_at"] == final_leave.isoformat()
        assert row["away_seconds"] == 60
        assert 1139 <= row["duration_seconds"] <= 1140
        summary = summarize(db, start, start + timedelta(hours=1), start + timedelta(hours=1))
        assert 1139 <= summary["total_seconds"] <= 1140

    asyncio.run(scenario())
