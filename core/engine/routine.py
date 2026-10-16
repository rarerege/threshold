"""Local-time arithmetic for the household's daily check-in window (pure functions)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.store.models import Household

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _tz(h: Household) -> ZoneInfo:
    return ZoneInfo(h.tz)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def local_date(h: Household, ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, _tz(h)).date()


def local_time_str(h: Household, ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, _tz(h)).strftime("%H:%M")


def day_bounds(h: Household, d: date) -> tuple[int, int]:
    """[start, end) of a local calendar day in epoch ms (DST-safe)."""
    tz = _tz(h)
    start = datetime.combine(d, time(0, 0), tz)
    end = datetime.combine(d + timedelta(days=1), time(0, 0), tz)
    return _ms(start), _ms(end)


def checkin_by_ms(h: Household, d: date) -> int:
    hh, mm = map(int, h.checkin_by.split(":"))
    return _ms(datetime.combine(d, time(hh, mm), _tz(h)))


def deadline_ms(h: Household, d: date) -> int:
    """When a day's check-in counts as missed: checkin_by + grace."""
    return checkin_by_ms(h, d) + h.grace_min * 60_000


def due_deadlines(h: Household, now_ms: int) -> list[tuple[date, int]]:
    """Days whose deadline passed in [monitoring_since, now], oldest first (usually 0 or 1)."""
    out = []
    d = local_date(h, h.monitoring_since) - timedelta(days=1)
    last = local_date(h, now_ms)
    while d <= last:
        dl = deadline_ms(h, d)
        if h.monitoring_since <= dl <= now_ms:
            out.append((d, dl))
        d += timedelta(days=1)
    return out


def weekday(d: date) -> str:
    return WEEKDAYS[d.weekday()]
