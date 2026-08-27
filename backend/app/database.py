from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


DEFAULT_SETTINGS: dict[str, Any] = {
    "sedentary_minutes": 60,
    "leave_grace_seconds": 180,
    "leave_confirm_seconds": 9,
    "merge_gap_seconds": 120,
    "sample_interval_seconds": 3,
    "daily_report_enabled": 1,
    "daily_report_time": "20:30",
    "weekly_report_enabled": 1,
    "weekly_report_day": 7,
    "weekly_report_time": "20:35",
    "bark_enabled": 0,
    "webhook_enabled": 0,
    "camera_offline_alert_enabled": 1,
    "camera_offline_minutes": 3,
    "camera_recovery_alert_enabled": 1,
    "reminder_title": "该起身活动啦",
    "reminder_body": "你已经连续坐了 {duration}，走动几分钟吧。",
    "roi_x": 0.13,
    "roi_y": 0.54,
    "roi_w": 0.37,
    "roi_h": 0.43,
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    sedentary_minutes INTEGER NOT NULL CHECK (sedentary_minutes BETWEEN 15 AND 360),
                    leave_grace_seconds INTEGER NOT NULL CHECK (leave_grace_seconds BETWEEN 10 AND 900),
                    leave_confirm_seconds INTEGER NOT NULL CHECK (leave_confirm_seconds BETWEEN 3 AND 120),
                    merge_gap_seconds INTEGER NOT NULL CHECK (merge_gap_seconds BETWEEN 15 AND 1800),
                    sample_interval_seconds INTEGER NOT NULL CHECK (sample_interval_seconds BETWEEN 1 AND 30),
                    daily_report_enabled INTEGER NOT NULL CHECK (daily_report_enabled IN (0, 1)),
                    daily_report_time TEXT NOT NULL,
                    weekly_report_enabled INTEGER NOT NULL CHECK (weekly_report_enabled IN (0, 1)),
                    weekly_report_day INTEGER NOT NULL CHECK (weekly_report_day BETWEEN 1 AND 7),
                    weekly_report_time TEXT NOT NULL,
                    bark_enabled INTEGER NOT NULL CHECK (bark_enabled IN (0, 1)),
                    webhook_enabled INTEGER NOT NULL CHECK (webhook_enabled IN (0, 1)),
                    camera_offline_alert_enabled INTEGER NOT NULL CHECK (camera_offline_alert_enabled IN (0, 1)),
                    camera_offline_minutes INTEGER NOT NULL CHECK (camera_offline_minutes BETWEEN 1 AND 120),
                    camera_recovery_alert_enabled INTEGER NOT NULL CHECK (camera_recovery_alert_enabled IN (0, 1)),
                    reminder_title TEXT NOT NULL,
                    reminder_body TEXT NOT NULL,
                    roi_x REAL NOT NULL, roi_y REAL NOT NULL, roi_w REAL NOT NULL, roi_h REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    is_sedentary INTEGER NOT NULL DEFAULT 0 CHECK (is_sedentary IN (0, 1)),
                    reminder_sent_at TEXT,
                    average_confidence REAL NOT NULL DEFAULT 0,
                    samples INTEGER NOT NULL DEFAULT 0,
                    away_seconds INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS session_breaks (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL CHECK (duration_seconds >= 0)
                );
                CREATE TABLE IF NOT EXISTS notification_log (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    detail TEXT NOT NULL DEFAULT '',
                    UNIQUE(kind, period_key)
                );
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_ended_at ON sessions(ended_at);
                CREATE INDEX IF NOT EXISTS idx_session_breaks_session_id ON session_breaks(session_id);
                CREATE INDEX IF NOT EXISTS idx_notification_log_sent_at ON notification_log(sent_at);
                """
            )
            # 从早期版本原位升级；新增列带默认值，不破坏已有设置。
            existing_columns = {row[1] for row in db.execute("PRAGMA table_info(settings)").fetchall()}
            migrations = {
                "camera_offline_alert_enabled": "ALTER TABLE settings ADD COLUMN camera_offline_alert_enabled INTEGER NOT NULL DEFAULT 1 CHECK (camera_offline_alert_enabled IN (0, 1))",
                "camera_offline_minutes": "ALTER TABLE settings ADD COLUMN camera_offline_minutes INTEGER NOT NULL DEFAULT 3 CHECK (camera_offline_minutes BETWEEN 1 AND 120)",
                "camera_recovery_alert_enabled": "ALTER TABLE settings ADD COLUMN camera_recovery_alert_enabled INTEGER NOT NULL DEFAULT 1 CHECK (camera_recovery_alert_enabled IN (0, 1))",
                "leave_confirm_seconds": "ALTER TABLE settings ADD COLUMN leave_confirm_seconds INTEGER NOT NULL DEFAULT 9 CHECK (leave_confirm_seconds BETWEEN 3 AND 120)",
                "merge_gap_seconds": "ALTER TABLE settings ADD COLUMN merge_gap_seconds INTEGER NOT NULL DEFAULT 120 CHECK (merge_gap_seconds BETWEEN 15 AND 1800)",
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    db.execute(statement)
            session_columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
            if "away_seconds" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN away_seconds INTEGER NOT NULL DEFAULT 0")
            values = {**DEFAULT_SETTINGS, "updated_at": datetime.now().astimezone().isoformat()}
            columns = ",".join(["id", *values.keys()])
            placeholders = ",".join(["?"] * (len(values) + 1))
            db.execute(
                f"INSERT OR IGNORE INTO settings ({columns}) VALUES ({placeholders})",
                [1, *values.values()],
            )
            roi_migration = db.execute("SELECT value FROM app_meta WHERE key = 'seat_roi_v2'").fetchone()
            if roi_migration is None:
                db.execute(
                    """UPDATE settings SET roi_x = 0.13, roi_y = 0.54, roi_w = 0.37, roi_h = 0.43, updated_at = ?
                       WHERE ABS(roi_x - 0.15) < 0.0001 AND ABS(roi_y - 0.15) < 0.0001
                         AND ABS(roi_w - 0.70) < 0.0001 AND ABS(roi_h - 0.80) < 0.0001""",
                    (datetime.now().astimezone().isoformat(),),
                )
                db.execute("INSERT INTO app_meta(key, value) VALUES ('seat_roi_v2', 'camera-2026-08-26')")
            db.execute("PRAGMA optimize")

    def settings(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        assert row is not None
        result = dict(row)
        for key in ("daily_report_enabled", "weekly_report_enabled", "bark_enabled", "webhook_enabled", "camera_offline_alert_enabled", "camera_recovery_alert_enabled"):
            result[key] = bool(result[key])
        result.pop("id", None)
        result.pop("updated_at", None)
        return result

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        clean = {key: value for key, value in values.items() if key in allowed}
        if not clean:
            return self.settings()
        for key in ("daily_report_enabled", "weekly_report_enabled", "bark_enabled", "webhook_enabled", "camera_offline_alert_enabled", "camera_recovery_alert_enabled"):
            if key in clean:
                clean[key] = int(bool(clean[key]))
        clean["updated_at"] = datetime.now().astimezone().isoformat()
        assignments = ",".join(f"{key} = ?" for key in clean)
        with self.connect() as db:
            db.execute(f"UPDATE settings SET {assignments} WHERE id = 1", list(clean.values()))
        return self.settings()

    def active_session(self) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def start_session(self, started_at: datetime) -> int:
        active = self.active_session()
        if active:
            return int(active["id"])
        with self.connect() as db:
            cursor = db.execute("INSERT INTO sessions(started_at) VALUES (?)", (started_at.isoformat(),))
            return int(cursor.lastrowid)

    def update_active(self, session_id: int, now: datetime, confidence: float, sedentary: bool) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE sessions SET duration_seconds = MAX(0, CAST((julianday(?) - julianday(started_at)) * 86400 AS INTEGER) - away_seconds),
                   is_sedentary = MAX(is_sedentary, ?), average_confidence = ((average_confidence * samples) + ?) / (samples + 1), samples = samples + 1
                   WHERE id = ? AND ended_at IS NULL""",
                (now.isoformat(), int(sedentary), confidence, session_id),
            )

    def end_session(self, session_id: int, ended_at: datetime, sedentary: bool) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE sessions SET ended_at = ?, duration_seconds = MAX(0, CAST((julianday(?) - julianday(started_at)) * 86400 AS INTEGER) - away_seconds),
                   is_sedentary = MAX(is_sedentary, ?) WHERE id = ? AND ended_at IS NULL""",
                (ended_at.isoformat(), ended_at.isoformat(), int(sedentary), session_id),
            )

    def add_break(self, session_id: int, started_at: datetime, ended_at: datetime) -> int:
        seconds = max(0, int((ended_at - started_at).total_seconds()))
        if seconds <= 0:
            return 0
        with self.connect() as db:
            db.execute(
                "INSERT INTO session_breaks(session_id, started_at, ended_at, duration_seconds) VALUES (?, ?, ?, ?)",
                (session_id, started_at.isoformat(), ended_at.isoformat(), seconds),
            )
            db.execute("UPDATE sessions SET away_seconds = away_seconds + ? WHERE id = ?", (seconds, session_id))
        return seconds

    def mark_reminder(self, session_id: int, sent_at: datetime) -> None:
        with self.connect() as db:
            db.execute("UPDATE sessions SET reminder_sent_at = ?, is_sedentary = 1 WHERE id = ?", (sent_at.isoformat(), session_id))

    def overlapping_sessions(self, start: datetime, end: datetime, now: datetime) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT * FROM sessions WHERE started_at < ? AND COALESCE(ended_at, ?) > ? ORDER BY started_at""",
                (end.isoformat(), now.isoformat(), start.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_sessions(self, start: datetime, end: datetime, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.overlapping_sessions(start, end, now)
        return list(reversed(rows[-limit:]))

    def breaks_for_sessions(self, session_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        if not session_ids:
            return {}
        placeholders = ",".join("?" for _ in session_ids)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM session_breaks WHERE session_id IN ({placeholders}) ORDER BY started_at",
                session_ids,
            ).fetchall()
        result: dict[int, list[dict[str, Any]]] = {session_id: [] for session_id in session_ids}
        for row in rows:
            item = dict(row)
            result[int(item["session_id"])].append(item)
        return result

    def notification_exists(self, kind: str, period_key: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM notification_log WHERE kind = ? AND period_key = ?", (kind, period_key)).fetchone() is not None

    def log_notification(self, kind: str, period_key: str, sent_at: datetime, success: bool, detail: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO notification_log(kind, period_key, sent_at, success, detail) VALUES (?, ?, ?, ?, ?)",
                (kind, period_key, sent_at.isoformat(), int(success), detail[:500]),
            )
