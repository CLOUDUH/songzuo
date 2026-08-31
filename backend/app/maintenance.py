from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .database import Database


def apply_requested_history_repairs(db: Database, timezone_name: str) -> bool:
    """按用户确认的真实在座时段修复 2026-08-28 至 2026-08-30。"""
    tz = ZoneInfo(timezone_name)
    range_start = datetime(2026, 8, 28, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    range_end = datetime(2026, 8, 31, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    local_replacements = [
        (datetime(2026, 8, 29, 14, 28, tzinfo=tz), datetime(2026, 8, 29, 14, 38, tzinfo=tz)),
        (datetime(2026, 8, 30, 12, 47, tzinfo=tz), datetime(2026, 8, 30, 13, 36, tzinfo=tz)),
        (datetime(2026, 8, 30, 16, 38, tzinfo=tz), datetime(2026, 8, 30, 17, 15, tzinfo=tz)),
    ]
    replacements = [(start.astimezone(timezone.utc), end.astimezone(timezone.utc)) for start, end in local_replacements]
    return db.replace_sessions_for_repair(
        "repair-history-2026-08-28-30-v1",
        range_start,
        range_end,
        replacements,
    )
