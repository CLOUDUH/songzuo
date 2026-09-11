from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
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
    breaks = db.breaks_for_sessions([int(row["id"]) for row in sessions])
    for row in sessions:
        row_start = max(start, parse_datetime(row["started_at"]))
        row_end = min(end, now, parse_datetime(row["ended_at"] or row["observed_until"] or row["started_at"]))
        seconds = max(0, int((row_end - row_start).total_seconds()))
        for item in breaks.get(int(row["id"]), []):
            break_start = max(row_start, parse_datetime(item["started_at"]))
            break_end = min(row_end, parse_datetime(item["ended_at"]))
            seconds -= max(0, int((break_end - break_start).total_seconds()))
        # 活跃会话的 duration_seconds 会在离座确认后冻结，避免把待合并的离座时间算作坐姿。
        if row["ended_at"] is None and parse_datetime(row["started_at"]) >= start:
            seconds = min(seconds, int(row["duration_seconds"]))
        seconds = max(0, seconds)
        total += seconds
        longest = max(longest, seconds)
        sedentary_count += int(bool(row["is_sedentary"]))
    return {"total_seconds": total, "longest_seconds": longest, "sedentary_count": sedentary_count, "session_count": len(sessions)}


def boundaries(now: datetime, tz: ZoneInfo) -> dict[str, tuple[datetime, datetime]]:
    local = now.astimezone(tz)
    day_start_local = datetime.combine(local.date(), time.min, tzinfo=tz)
    week_start_local = day_start_local - timedelta(days=local.weekday())
    month_start_local = day_start_local.replace(day=1)
    next_month_local = _next_month(month_start_local)
    return {
        "day": (day_start_local.astimezone(timezone.utc), (day_start_local + timedelta(days=1)).astimezone(timezone.utc)),
        "week": (week_start_local.astimezone(timezone.utc), (week_start_local + timedelta(days=7)).astimezone(timezone.utc)),
        "month": (month_start_local.astimezone(timezone.utc), next_month_local.astimezone(timezone.utc)),
    }


def _next_month(value: datetime) -> datetime:
    return value.replace(year=value.year + 1, month=1) if value.month == 12 else value.replace(month=value.month + 1)


def _previous_month(value: datetime) -> datetime:
    previous_day = value - timedelta(days=1)
    return previous_day.replace(day=1)


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
        while cursor.month == month.month and cursor <= local:
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
        summary = summarize(db, local_start.astimezone(timezone.utc), min(local_end.astimezone(timezone.utc), now), now)
        result.append({"label": label, "seconds": summary["total_seconds"], "sedentary_count": summary["sedentary_count"]})
    return result


def same_time_comparison(db: Database, now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    local = now.astimezone(tz)
    local_midnight = datetime.combine(local.date(), time.min, tzinfo=tz)
    elapsed = local - local_midnight
    today = summarize(db, local_midnight.astimezone(timezone.utc), now, now)["total_seconds"]

    def average_for_days(days: list[date]) -> tuple[int, int]:
        values = []
        for day in days:
            start_local = datetime.combine(day, time.min, tzinfo=tz)
            end_local = start_local + elapsed
            day_summary = summarize(db, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), now)
            if day_summary["session_count"] > 0:
                values.append(day_summary["total_seconds"])
        return (int(sum(values) / len(values)), len(values)) if values else (0, 0)

    weekly_days = [local.date() - timedelta(days=index) for index in range(1, 8)]
    month_start = local.date().replace(day=1)
    monthly_days = [month_start + timedelta(days=index) for index in range((local.date() - month_start).days)]
    if not monthly_days:
        monthly_days = [local.date() - timedelta(days=index) for index in range(1, 31)]
    week_average, week_samples = average_for_days(weekly_days)
    month_average, month_samples = average_for_days(monthly_days)

    def change(average: int) -> float | None:
        return round((today - average) / average * 100, 1) if average > 0 else None

    return {
        "as_of": local.strftime("%H:%M"),
        "today_seconds": today,
        "week_average_seconds": week_average,
        "month_average_seconds": month_average,
        "week_change_percent": change(week_average),
        "month_change_percent": change(month_average),
        "week_sample_days": week_samples,
        "month_sample_days": month_samples,
    }


def period_report(db: Database, period: str, now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    spans = boundaries(now, tz)
    start, planned_end = spans[period]
    end = min(planned_end, now)
    summary = summarize(db, start, end, now)
    local_start = start.astimezone(tz)
    if period == "week":
        previous_start = start - timedelta(days=7)
    else:
        previous_start = _previous_month(local_start).astimezone(timezone.utc)
    previous_end = min(previous_start + (end - start), start)
    previous = summarize(db, previous_start, previous_end, now)
    elapsed_days = max(1, (end.astimezone(tz).date() - local_start.date()).days + 1)

    def change(current: int, baseline: int) -> float | None:
        return round((current - baseline) / baseline * 100, 1) if baseline > 0 else None

    return {
        "period": period,
        "summary": summary,
        "previous": previous,
        "daily_average_seconds": int(summary["total_seconds"] / elapsed_days),
        "total_change_percent": change(summary["total_seconds"], previous["total_seconds"]),
        "sedentary_change_percent": change(summary["sedentary_count"], previous["sedentary_count"]),
        "series": series(db, period, now, tz),
    }


def day_detail(db: Database, target_date: date, now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    local_start = datetime.combine(target_date, time.min, tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    start = local_start.astimezone(timezone.utc)
    end = local_end.astimezone(timezone.utc)
    effective_end = min(end, now) if target_date == now.astimezone(tz).date() else end
    summary = summarize(db, start, effective_end, now)
    sessions = db.overlapping_sessions(start, end, now)
    break_map = db.breaks_for_sessions([int(row["id"]) for row in sessions])
    total_break_seconds = 0
    for row in sessions:
        row["is_sedentary"] = bool(row["is_sedentary"])
        row["breaks"] = break_map.get(int(row["id"]), [])
        total_break_seconds += sum(int(item["duration_seconds"]) for item in row["breaks"])
    first_started_at = sessions[0]["started_at"] if sessions else None
    last_ended_at = (sessions[-1]["ended_at"] or sessions[-1]["observed_until"] or sessions[-1]["started_at"]) if sessions else None
    span_seconds = 0
    if first_started_at and last_ended_at:
        span_seconds = max(0, int((min(parse_datetime(last_ended_at), end) - max(parse_datetime(first_started_at), start)).total_seconds()))
    seated_ratio = round(summary["total_seconds"] / span_seconds * 100, 1) if span_seconds else 0.0
    return {
        "date": target_date.isoformat(),
        "summary": summary,
        "sessions": sessions,
        "interruptions": db.interruptions(start, end, now),
        "analysis": {
            "first_started_at": first_started_at,
            "last_ended_at": last_ended_at,
            "total_break_seconds": total_break_seconds,
            "average_session_seconds": int(summary["total_seconds"] / summary["session_count"]) if summary["session_count"] else 0,
            "seated_ratio_percent": seated_ratio,
        },
    }


def format_duration(seconds: int) -> str:
    minutes = max(0, seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分" if hours else f"{minutes}分钟"
