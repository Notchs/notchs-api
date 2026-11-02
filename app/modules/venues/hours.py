"""Open or closed right now, from `venues.opening_hours` ({"mon": [["08:00", "23:00"]], ...}).

A range whose closing time is earlier than its opening time runs past midnight: Friday
["18:00", "02:00"] is still open at 01:30 on Saturday. A missing or empty day means closed.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True, slots=True)
class OpenStatus:
    # None when the venue has not set its hours.
    is_open: bool | None
    # "Open until 11 PM", "Opens 8 AM", "Opens Mon 8 AM"; None without hours.
    label: str | None
    closes_at: str | None = None  # "23:00", while open
    opens_at: str | None = None  # "08:00", while closed


def _parse(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def clock_label(value: str) -> str:
    """`23:00` -> `11 PM`, `08:30` -> `8:30 AM`, `00:00` -> `12 AM`."""
    moment = _parse(value)
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour} {suffix}" if moment.minute == 0 else f"{hour}:{moment.minute:02d} {suffix}"


def _intervals(hours: dict[str, Any], day: datetime) -> list[tuple[datetime, datetime, str, str]]:
    """Every opening that touches `day`: its own ranges and the night before's late ones."""
    out = []
    for back in (1, 0):
        start_day = (day - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        for opens, closes in hours.get(DAYS[start_day.weekday()]) or []:
            start = datetime.combine(start_day.date(), _parse(opens), day.tzinfo)
            end = datetime.combine(start_day.date(), _parse(closes), day.tzinfo)
            if end <= start:
                end += timedelta(days=1)
            out.append((start, end, opens, closes))
    return out


def status_at(hours: dict[str, Any] | None, now_local: datetime) -> OpenStatus:
    """`now_local` must be in the venue's city timezone."""
    if not hours or not any(hours.get(day) for day in DAYS):
        return OpenStatus(is_open=None, label=None)
    for start, end, _, closes in _intervals(hours, now_local):
        if start <= now_local < end:
            return OpenStatus(True, f"Open until {clock_label(closes)}", closes_at=closes)
    for ahead in range(8):
        day = now_local + timedelta(days=ahead)
        upcoming = sorted(
            (start, opens)
            for start, _, opens, _ in _intervals(hours, day)
            if start > now_local and start.date() == day.date()
        )
        if upcoming:
            start, opens = upcoming[0]
            when = "" if ahead == 0 else f"{start.strftime('%a')} "
            return OpenStatus(False, f"Opens {when}{clock_label(opens)}", opens_at=opens)
    return OpenStatus(False, "Closed")
