"""UTC calendar-month billing periods.

The quota window is the calendar month in UTC, resetting on the 1st at 00:00Z.
``now()`` is injectable so month-boundary behaviour is deterministically testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def now() -> datetime:
    """Current time, UTC-aware. Patch this in tests to travel through time."""
    return datetime.now(UTC)


def period_start(at: datetime | None = None) -> datetime:
    """00:00:00Z on the 1st of the month containing ``at``."""
    at = at or now()
    return at.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def period_end(at: datetime | None = None) -> datetime:
    """00:00:00Z on the 1st of the FOLLOWING month -- exclusive upper bound."""
    start = period_start(at)
    # Step into the next month by jumping past the longest possible month, then
    # snapping back to the 1st. Avoids any month-length arithmetic.
    return period_start(start + timedelta(days=32))


def seconds_until_reset(at: datetime | None = None) -> int:
    """Seconds until the quota window rolls over.

    This is the value of the ``Retry-After`` header on a 429: it is the moment a
    retry can actually succeed, rather than an arbitrary backoff (rule Q2).
    """
    at = at or now()
    return max(1, int((period_end(at) - at.astimezone(UTC)).total_seconds()))
