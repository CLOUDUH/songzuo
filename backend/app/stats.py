from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .database import Database


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def summarize(db: Database, start: datetime, end: datetime, now: datetime) -> dict[str, int]:
    total = 0
    longest = 0
    sedentary_count = 0
    sessions = db.overlapping_sessions(start, end, now)
    for row in sessions:
        row_start = max(start, parse_datetime(row["started_at"]))
        row_end = min(end, parse_datetime(row["ended_at"]) if row["ended_at"] else now)
        seconds = max(0, int((row_end - row_start).total_seconds()))
        total += seconds
        longest = max(longest, seconds)
        sedentary_count += int(bool(row["is_sedentary"]))
    return {"total_seconds": total, "longest_seconds": longest, "sedentary_count": sedentary_count, "session_count": len(sessions)}


def boundaries(now: datetime, tz: ZoneInfo) -> dict[str, tuple[datetime, datetime]]:
    local = now.astimezone(tz)
    day_start_local = datetime.combine(local.date(), time.min, tzinfo=tz)
    week_start_local = day_start_local - timedelta(days=local.weekday())
    month_start_local = day_start_local.replace(day=1)
    if month_start_local.month == 12:
        next_month_local = month_start_local.replace(year=month_start_local.year + 1, month=1)
    else:
        next_month_local = month_start_local.replace(month=month_start_local.month + 1)
    return {
        "day": (day_start_local.astimezone(timezone.utc), (day_start_local + timedelta(days=1)).astimezone(timezone.utc)),
        "week": (week_start_local.astimezone(timezone.utc), (week_start_local + timedelta(days=7)).astimezone(timezone.utc)),
        "month": (month_start_local.astimezone(timezone.utc), next_month_local.astimezone(timezone.utc)),
    }


def series(db: Database, period: str, now: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    local = now.astimezone(tz)
    slots: list[tuple[str, datetime, datetime]] = []
    if period == "day":
        day = datetime.combine(local.date(), time.min, tzinfo=tz)
        for hour in range(0, 24, 3):
            start = day + timedelta(hours=hour)
            slots.append((f"{hour:02d}", start, start + timedelta(hours=3)))
    elif period == "month":
        month = datetime.combine(local.date().replace(day=1), time.min, tzinfo=tz)
        cursor = month
        while cursor.month == month.month:
            slots.append((str(cursor.day), cursor, cursor + timedelta(days=1)))
            cursor += timedelta(days=1)
    else:
        week = datetime.combine(local.date(), time.min, tzinfo=tz) - timedelta(days=local.weekday())
        labels = "一二三四五六日"
        for index, label in enumerate(labels):
            start = week + timedelta(days=index)
            slots.append((label, start, start + timedelta(days=1)))
    result = []
    for label, local_start, local_end in slots:
        summary = summarize(db, local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc), now)
        result.append({"label": label, "seconds": summary["total_seconds"], "sedentary_count": summary["sedentary_count"]})
    return result


def format_duration(seconds: int) -> str:
    minutes = max(0, seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分" if hours else f"{minutes}分钟"
