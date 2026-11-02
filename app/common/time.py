"""Business time. Days ("one stamp per venue per day") are counted in India."""

from datetime import date, datetime, timedelta, timezone

# India has one offset all year and no daylight saving.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def ist_date(moment: datetime) -> date:
    """The calendar day in India at `moment` (which must be timezone-aware)."""
    return moment.astimezone(IST).date()
