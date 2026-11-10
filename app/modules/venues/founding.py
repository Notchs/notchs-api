"""Founding places and the free months (team decisions, 10 Nov 2026).

    founding place   the first 100 places of each kind to join Notchs (first 200 turfs, box
                     grounds and gyms) get 25% off their subscription for life
    free months      every place pays nothing for its first 3 months; after that the
                     subscription charges apply (the amount isn't decided yet)

A place gets its founding number when the team approves it (its venue is created): #1, #2...
within its kind, never reused. Numbers are only ever given from real approvals.
"""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.venues.models import Venue, VenueCategory

FOUNDING_DISCOUNT_PERCENT = 25
FREE_MONTHS = 3
DEFAULT_FOUNDING_SPOTS = 100
FOUNDING_SPOTS: dict[str, int] = {
    # Box grounds count as turfs (team decision).
    VenueCategory.TURF_GYM: 200,
}


def founding_spots(category: str) -> int:
    return FOUNDING_SPOTS.get(category, DEFAULT_FOUNDING_SPOTS)


async def founding_taken(session: AsyncSession, category: str) -> int:
    """How many founding numbers this kind of place has given out (the highest number)."""
    return (
        await session.scalar(
            select(func.coalesce(func.max(Venue.founding_venue_no), 0)).where(
                Venue.category == category
            )
        )
        or 0
    )


async def next_founding_no(session: AsyncSession, category: str) -> int | None:
    """The next founding number for this kind, or None once its spots are gone. Holds a lock
    for the rest of the transaction, so two approvals at once can't take the same number."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"founding:{category}"}
    )
    number = await founding_taken(session, category) + 1
    return number if number <= founding_spots(category) else None
