"""Date helpers anchored to the user's timezone (Asia/Kuala_Lumpur).

Storage stays UTC; "today" and review/window semantics use KL time.
The IANA zone is loaded via :mod:`zoneinfo`; on hosts without the IANA
database (e.g. Windows without the ``tzdata`` wheel) a fixed UTC+8 offset is
used instead — exact for Malaysia, which has observed UTC+8 with no DST
since 1990.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _load_kl_zone() -> tzinfo:
    """Load Asia/Kuala_Lumpur, falling back to a fixed UTC+8 offset when the
    IANA database is unavailable on the host."""
    try:
        return ZoneInfo("Asia/Kuala_Lumpur")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Kuala_Lumpur")


KL_TZ: Final[tzinfo] = _load_kl_zone()


def kl_today() -> date:
    """Return today's calendar date in Asia/Kuala_Lumpur."""
    return datetime.now(KL_TZ).date()


def utc_to_kl_date(dt: datetime) -> date:
    """Return the KL calendar date of a stored UTC datetime.

    Naive datetimes are interpreted as UTC (SQLite stores naive UTC);
    timezone-aware datetimes are converted directly.
    """
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(KL_TZ).date()


def add_months(d: date, months: int) -> date:
    """Return ``d`` shifted by ``months`` calendar months (may be negative).

    The day-of-month is clamped to the target month's last day
    (e.g. 2026-01-31 + 1 month -> 2026-02-28).
    """
    total = d.year * 12 + (d.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def rolling_window_start(end: date, days: int) -> date:
    """Return the first date of the inclusive ``days``-day window ending at
    ``end`` (e.g. days=7 with end=Sunday starts the previous Monday)."""
    if days < 1:
        raise ValueError("Rolling window length must be at least 1 day")
    return end - timedelta(days=days - 1)


def in_rolling_window(d: date, end: date, days: int) -> bool:
    """Return True when ``d`` falls inside the inclusive ``days``-day window
    ending at ``end``."""
    return rolling_window_start(end, days) <= d <= end
