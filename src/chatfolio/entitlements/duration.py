import calendar
from datetime import datetime, timedelta

from chatfolio.models.billing import DurationUnit


def add_months(start: datetime, months: int) -> datetime:
    """Calendar-month arithmetic that clamps the day (Jan 31 + 1 month = Feb 28/29)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def compute_expiry(start: datetime, unit: DurationUnit, count: int) -> datetime | None:
    """When a plan purchased at `start` ends; None means it never expires (lifetime)."""
    if unit == DurationUnit.LIFETIME:
        return None
    if unit == DurationUnit.DAY:
        return start + timedelta(days=count)
    if unit == DurationUnit.MONTH:
        return add_months(start, count)
    return add_months(start, count * 12)
