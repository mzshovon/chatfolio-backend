from datetime import UTC, datetime, timedelta

from chatfolio.models.billing import QuotaPeriod

_GRACE = timedelta(days=3)


def period_key(period: QuotaPeriod, now: datetime | None = None) -> str:
    """Stable id of the quota window `now` falls in (UTC): "2026-10", "2026-10-16", "lifetime"."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if period == QuotaPeriod.MONTHLY:
        return now.strftime("%Y-%m")
    if period == QuotaPeriod.DAILY:
        return now.strftime("%Y-%m-%d")
    return "lifetime"


def seconds_until_period_end(period: QuotaPeriod, now: datetime | None = None) -> int | None:
    """How long a Redis counter for this window must live (period end + a grace); None = forever."""
    if period == QuotaPeriod.LIFETIME:
        return None
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if period == QuotaPeriod.DAILY:
        end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        end = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return int((end - now + _GRACE).total_seconds())


def period_start(period: QuotaPeriod, now: datetime | None = None) -> datetime | None:
    """Start of the current window (UTC), or None for lifetime."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if period == QuotaPeriod.LIFETIME:
        return None
    if period == QuotaPeriod.DAILY:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
