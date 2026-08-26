from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.database import Database  # noqa: E402
from app.stats import summarize  # noqa: E402


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
    assert "camera_password" not in settings
