"""Days, weeks and months in a city's own timezone (IST for Ahmedabad).

"One Notch per place per day", "this week" and "this month" all follow the city's clock, so
a Notch at 00:30 IST belongs to the new day even though it is the previous day in UTC.
Weeks run Monday to Sunday (rankings are "updated every Monday").
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.modules.locations.models import DEFAULT_TIMEZONE


@lru_cache(maxsize=32)
def zone(name: str | None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_TIMEZONE)


def local(moment: datetime, tz: str | None) -> datetime:
    return moment.astimezone(zone(tz))


def today(tz: str | None, now: datetime | None = None) -> date:
    return local(now or datetime.now(UTC), tz).date()


@dataclass(frozen=True, slots=True)
class Period:
    """Inclusive first and last day."""

    start: date
    end: date

    def __contains__(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def month_of(day: date) -> Period:
    first = day.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    return Period(first, next_first - timedelta(days=1))


def previous_month(day: date) -> Period:
    return month_of(day.replace(day=1) - timedelta(days=1))


def week_of(day: date) -> Period:
    monday = day - timedelta(days=day.weekday())
    return Period(monday, monday + timedelta(days=6))


def last_days(day: date, count: int) -> Period:
    """`count` days ending with `day`: the venue app's "7 days" and "30 days"."""
    return Period(day - timedelta(days=count - 1), day)


def month_from_string(value: str) -> Period:
    """`2026-10` -> October 2026. Raises ValueError for anything else."""
    year, _, month = value.partition("-")
    return month_of(date(int(year), int(month), 1))


def month_label(day: date) -> str:
    return day.strftime("%B")  # "October"
