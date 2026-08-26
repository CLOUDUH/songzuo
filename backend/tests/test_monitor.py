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


def test_monitor_debounces_and_records_original_leave_time(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        db.initialize()
        db.update_settings({"sedentary_minutes": 15, "leave_grace_seconds": 60, "sample_interval_seconds": 3, "bark_enabled": True})
        notifier = FakeNotifier()
        monitor = Monitor(db, FakeCamera(), FakeDetector(), notifier)  # type: ignore[arg-type]
        settings = db.settings()
        start = datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc)
        await monitor.process(Detection(True, 0.9), start, settings)
        assert not monitor.occupied
        await monitor.process(Detection(True, 0.9), start + timedelta(seconds=7), settings)
        assert monitor.occupied
        await monitor.process(Detection(True, 0.95), start + timedelta(minutes=16), settings)
        assert len(notifier.messages) == 1
        first_absence = start + timedelta(minutes=20)
        await monitor.process(Detection(False, 0), first_absence, settings)
        await monitor.process(Detection(False, 0), first_absence + timedelta(seconds=61), settings)
        row = db.overlapping_sessions(start, start + timedelta(hours=1), start + timedelta(hours=1))[0]
        assert row["ended_at"] == first_absence.isoformat()
        assert row["is_sedentary"] == 1

    asyncio.run(scenario())
