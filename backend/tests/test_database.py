from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.database import Database  # noqa: E402
from app.maintenance import apply_requested_history_repairs  # noqa: E402
from app.stats import day_detail, same_time_comparison, summarize  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402


def test_session_lifecycle_and_cross_boundary_summary(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    start = datetime(2026, 8, 25, 23, 50, tzinfo=timezone.utc)
    end = start + timedelta(minutes=80)
    session_id = db.start_session(start)
    db.update_active(session_id, start + timedelta(minutes=65), 0.9, True)
    db.end_session(session_id, end, True)

    midnight = datetime(2026, 8, 26, tzinfo=timezone.utc)
    result = summarize(db, midnight, midnight + timedelta(days=1), end)
    assert result["total_seconds"] == 70 * 60
    assert result["session_count"] == 1
    assert result["sedentary_count"] == 1


def test_settings_are_persisted_and_secrets_are_not_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    settings = db.update_settings({"sedentary_minutes": 75, "camera_password": "never-store-this"})
    assert settings["sedentary_minutes"] == 75
    assert settings["camera_offline_alert_enabled"] is True
    assert settings["camera_offline_minutes"] == 3
    assert settings["face_confidence_threshold"] == 0.60
    assert settings["min_face_width_ratio"] == 0.09
    assert (settings["roi_x"], settings["roi_y"], settings["roi_w"], settings["roi_h"]) == (0.13, 0.54, 0.37, 0.43)
    assert "camera_password" not in settings


def test_old_default_roi_is_migrated_once(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    with db.connect() as connection:
        connection.execute("UPDATE settings SET roi_x = 0.15, roi_y = 0.15, roi_w = 0.70, roi_h = 0.80 WHERE id = 1")
        connection.execute("DELETE FROM app_meta WHERE key = 'seat_roi_v2'")
    db.initialize()
    settings = db.settings()
    assert (settings["roi_x"], settings["roi_y"], settings["roi_w"], settings["roi_h"]) == (0.13, 0.54, 0.37, 0.43)


def test_same_time_comparison_uses_historical_days_at_same_clock_time(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc)  # 当地 10:00
    for days_ago, minutes in ((1, 60), (2, 120)):
        start = datetime(2026, 8, 27 - days_ago, 0, 0, tzinfo=timezone.utc)
        session_id = db.start_session(start)
        db.end_session(session_id, start + timedelta(minutes=minutes), minutes >= 60)
    today_start = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)
    session_id = db.start_session(today_start)
    db.end_session(session_id, today_start + timedelta(minutes=30), False)
    result = same_time_comparison(db, now, tz)
    assert result["today_seconds"] == 30 * 60
    assert result["week_average_seconds"] > 0
    assert result["week_change_percent"] < 0


def test_requested_august_history_repair_is_exact_and_recoverable(tmp_path: Path) -> None:
    db = Database(tmp_path / "repair.db")
    db.initialize()
    db.update_settings({"sedentary_minutes": 30})
    old_start = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    old_id = db.start_session(old_start)
    db.end_session(old_id, old_start + timedelta(hours=20), True)
    assert apply_requested_history_repairs(db, "Asia/Shanghai") is True
    assert apply_requested_history_repairs(db, "Asia/Shanghai") is False
    backup = db.repair_backup("repair-history-2026-08-28-30-v1")
    assert backup is not None and len(backup["sessions"]) == 1

    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    day28 = day_detail(db, datetime(2026, 8, 28).date(), now, tz)
    day29 = day_detail(db, datetime(2026, 8, 29).date(), now, tz)
    day30 = day_detail(db, datetime(2026, 8, 30).date(), now, tz)
    assert day28["summary"]["session_count"] == 0
    assert [row["duration_seconds"] for row in day29["sessions"]] == [600]
    assert [row["duration_seconds"] for row in day30["sessions"]] == [2940, 2220]
    assert day30["summary"]["sedentary_count"] == 2
