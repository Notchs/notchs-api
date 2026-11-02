"""Founding Venues: the first 30 venues, with 2 months free (QR spec, "Test data & pricing").

A venue gets the next number when the Notchs team approves its application, and its free
months start that day ("Free until 14 Dec 2026 for a venue that joined 14 Oct").
"""

import calendar
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.modules.venues.models import MAX_FOUNDING_VENUE_NO, Venue

FOUNDING_FREE_MONTHS = 2
_ATTEMPTS = 5


def add_months(day: date, months: int) -> date:
    """14 Oct + 2 months = 14 Dec; 31 Dec + 2 months = 28 (or 29) Feb."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


async def seats_left(session: AsyncSession) -> int:
    taken = await session.scalar(select(func.count()).where(Venue.founding_venue_no.is_not(None)))
    return max(0, MAX_FOUNDING_VENUE_NO - (taken or 0))


async def assign(session: AsyncSession, venue: Venue, *, today: date | None = None) -> int | None:
    """Give the venue the lowest free number and its free months, if a seat is left."""
    if venue.founding_venue_no is not None:
        return venue.founding_venue_no
    start = today or datetime.now(UTC).date()
    for _ in range(_ATTEMPTS):
        taken = set(
            await session.scalars(
                select(Venue.founding_venue_no).where(Venue.founding_venue_no.is_not(None))
            )
        )
        number = next((n for n in range(1, MAX_FOUNDING_VENUE_NO + 1) if n not in taken), None)
        if number is None:
            return None
        free_until = add_months(start, FOUNDING_FREE_MONTHS)
        try:
            async with session.begin_nested():
                await session.execute(
                    update(Venue)
                    .where(Venue.id == venue.id, Venue.founding_venue_no.is_(None))
                    .values(founding_venue_no=number, free_until=free_until)
                    .execution_options(synchronize_session=False)
                )
        except IntegrityError:
            continue  # taken a moment ago by another approval
        set_committed_value(venue, "founding_venue_no", number)
        set_committed_value(venue, "free_until", free_until)
        return number
    return None
