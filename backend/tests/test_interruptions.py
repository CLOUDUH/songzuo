import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.database import Database
from app.monitor import Monitor
from app.detector import Detection
from app.stats import summarize
from app.repair_interruption import repair, restore


def test_offline_cross_day_and_recovery_do_not_accrue_sitting(tmp_path):
    async def scenario():
        db = Database(tmp_path / 'test.db')
        db.initialize()
        settings = db.settings()
        monitor = Monitor(db, None, None, None)
        start = datetime(2026, 9, 9, 3, 33, tzinfo=timezone.utc)
        await monitor.process(Detection(True, .9), start, settings)
        last = start + timedelta(seconds=6)
        await monitor.process(Detection(True, .9), last, settings)
        session_id = monitor.session_id
        offline = SimpleNamespace(online=False, captured_at=last, jpeg=b'old-frame')
        await monitor.poll(offline, start + timedelta(seconds=10), settings)
        await monitor.poll(offline, start + timedelta(days=1), settings)
        assert db.active_session() is None
        assert monitor.monitoring_available is False
        next_day = start.replace(hour=0, minute=0) + timedelta(days=1)
        assert summarize(db, next_day, next_day + timedelta(days=1), next_day + timedelta(days=1))['total_seconds'] == 0
        recovered = start + timedelta(days=2)
        await monitor.process(Detection(True, .9), recovered, settings)
        assert monitor.session_id is None
        await monitor.process(Detection(True, .9), recovered + timedelta(seconds=6), settings)
        assert monitor.session_id != session_id
        assert monitor.seated_elapsed(recovered + timedelta(seconds=6)) == 6
        rows = db.interruptions(start, recovered + timedelta(seconds=10), recovered + timedelta(seconds=10))
        assert len(rows) == 1
        assert rows[0]['started_at'] == last.isoformat()
        assert rows[0]['ended_at'] == recovered.isoformat()
    asyncio.run(scenario())


def test_stale_frame_and_service_restart_are_unknown(tmp_path):
    async def scenario():
        db = Database(tmp_path / 'test.db')
        db.initialize()
        settings = db.settings()
        start = datetime(2026, 9, 9, 3, 33, tzinfo=timezone.utc)
        monitor = Monitor(db, None, None, None)
        await monitor.process(Detection(True, .9), start, settings)
        await monitor.process(Detection(True, .9), start + timedelta(seconds=6), settings)
        restarted = Monitor(db, None, None, None)
        assert db.active_session() is None
        await restarted.process(Detection(True, .9), start + timedelta(days=1), settings)
        assert restarted.session_id is None
        await restarted.process(Detection(True, .9), start + timedelta(days=1, seconds=6), settings)
        stale = SimpleNamespace(online=True, captured_at=start + timedelta(days=1, seconds=6), jpeg=b'old')
        await restarted.poll(stale, start + timedelta(days=1, seconds=50), settings)
        assert restarted.session_id is None
        assert not restarted.monitoring_available
    asyncio.run(scenario())


def test_frozen_cross_midnight_summary_never_uses_wall_clock(tmp_path):
    db = Database(tmp_path / 'test.db')
    db.initialize()
    start = datetime(2026, 9, 9, 23, 59, tzinfo=timezone.utc)
    sid = db.start_session(start)
    last = start + timedelta(seconds=10)
    db.update_active(sid, last, .9, False)
    midnight = start.replace(hour=0, minute=0) + timedelta(days=1)
    assert summarize(db, midnight, midnight + timedelta(days=1), midnight + timedelta(days=1))['total_seconds'] == 0
    assert 9 <= summarize(db, start, midnight, midnight + timedelta(days=1))['total_seconds'] <= 10


def test_repair_is_scoped_idempotent_and_restorable(tmp_path):
    db = Database(tmp_path / 'test.db')
    db.initialize()
    start = datetime(2026, 9, 9, 3, 33, tzinfo=timezone.utc)
    sid = db.start_session(start)
    end = start + timedelta(days=2)
    db.add_break(sid, start + timedelta(minutes=1), start + timedelta(minutes=2))
    db.end_session(sid, end, True)
    normal = db.start_session(end + timedelta(minutes=1))
    db.end_session(normal, end + timedelta(minutes=10), False)
    key = repair(db, sid, start.isoformat())
    assert repair(db, sid, start.isoformat()) == key
    assert len(db.overlapping_sessions(start, end + timedelta(hours=1), end + timedelta(hours=1))) == 1
    assert len(db.interruptions(start, end, end)) == 1
    restore(db, key)
    assert len(db.overlapping_sessions(start, end + timedelta(hours=1), end + timedelta(hours=1))) == 2
    assert len(db.breaks_for_sessions([sid])[sid]) == 1
    assert db.interruptions(start, end, end) == []
