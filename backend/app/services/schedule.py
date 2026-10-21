"""Timezone-aware sending-window arithmetic (DST-safe via zoneinfo)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def local_to_utc(d: date, t: time, tz: ZoneInfo) -> datetime:
    """Interpret a wall-clock time in `tz`. Nonexistent times (spring-forward gap) shift forward."""
    # fold=0: ambiguous times (fall-back) resolve to the first occurrence; times inside a
    # spring-forward gap use the pre-transition offset, which lands just after the gap.
    return datetime.combine(d, t).replace(tzinfo=tz).astimezone(UTC)


def in_window(now: datetime, tz_name: str, start: str, end: str, days: list[int]) -> bool:
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    if local.weekday() not in days:
        return False
    t = local.time().replace(tzinfo=None)
    return parse_hhmm(start) <= t < parse_hhmm(end)


def next_window_start(now: datetime, tz_name: str, start: str, end: str, days: list[int]) -> datetime:
    """Earliest instant >= now that falls inside the sending window (returned in UTC)."""
    if not days:
        raise ValueError("No sending days configured")
    if in_window(now, tz_name, start, end, days):
        return now
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    s = parse_hhmm(start)
    for offset in range(0, 15):
        d = (local + timedelta(days=offset)).date()
        if d.weekday() not in days:
            continue
        candidate = local_to_utc(d, s, tz)
        if candidate >= now:
            return candidate
    raise ValueError("Could not find a sending window in the next two weeks")


def add_delay(base: datetime, tz_name: str, days: int, hours: int) -> datetime:
    """Add a follow-up delay using wall-clock days in the campaign timezone (DST-aware)."""
    tz = ZoneInfo(tz_name)
    local = base.astimezone(tz)
    naive = local.replace(tzinfo=None) + timedelta(days=days)
    shifted = local_to_utc(naive.date(), naive.time(), tz)
    return shifted + timedelta(hours=hours)


def estimate_schedule(
    count: int,
    start: datetime,
    tz_name: str,
    window_start: str,
    window_end: str,
    days: list[int],
    per_day: int,
    min_delay_seconds: int,
) -> list[dict[str, object]]:
    """Rough per-day plan for `count` first-step emails. Assumes no failures or retries."""
    plan: list[dict[str, object]] = []
    if count <= 0 or per_day <= 0:
        return plan
    tz = ZoneInfo(tz_name)
    window_len = (
        datetime.combine(date.today(), parse_hhmm(window_end)) - datetime.combine(date.today(), parse_hhmm(window_start))
    ).total_seconds()
    capacity_by_spacing = max(1, int(window_len // max(min_delay_seconds, 1)))
    daily = min(per_day, capacity_by_spacing)
    remaining = count
    cursor = next_window_start(start, tz_name, window_start, window_end, days)
    first_day = True
    while remaining > 0 and len(plan) < 400:
        local_day = cursor.astimezone(tz).date()
        if first_day:
            end_dt = local_to_utc(local_day, parse_hhmm(window_end), tz)
            today_capacity = max(0, min(daily, int((end_dt - cursor).total_seconds() // max(min_delay_seconds, 1)) + 1))
            first_day = False
        else:
            today_capacity = daily
        n = min(remaining, today_capacity)
        if n > 0:
            plan.append({"date": local_day.isoformat(), "emails": n})
            remaining -= n
        nxt = local_to_utc(local_day + timedelta(days=1), parse_hhmm(window_start), tz)
        cursor = next_window_start(nxt, tz_name, window_start, window_end, days)
    return plan
