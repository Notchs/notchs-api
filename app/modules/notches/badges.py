"""The pilot's five badges: their rules, awarding them after a Notch, and progress towards them.

    First Notch       one of the first `venue.first_notch_spots` customers (50) at a venue
    Founding Member   one of the first 500 customers (users.founding_member_no)
    Cafe Hopper       Notchs at CAFE_HOPPER_CAFES different cafés
    Night Owl         NIGHT_OWL_VISITS Notchs at or after 9 PM (until 4 AM)
    Regular           REGULAR_VISITS Notchs at one place in one calendar month

Only counted Notchs (verified or flagged) count. A badge is earned once and never taken back,
even if a Notch is later rejected.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ids import new_uuid
from app.modules.locations.time import month_of
from app.modules.notches.models import COUNTED_STATUSES, Badge, Notch, UserBadge
from app.modules.users.models import MAX_FOUNDING_MEMBER_NO, User
from app.modules.venues.models import Venue, VenueCategory

REGULAR_VISITS = 5
NIGHT_OWL_VISITS = 5
NIGHT_STARTS_HOUR = 21
NIGHT_ENDS_HOUR = 4  # exclusive: 00:00-03:59 still counts as the night before
CAFE_HOPPER_CAFES = 5


@dataclass(frozen=True, slots=True)
class BadgeInfo:
    name: str
    how: str  # how it is earned, for the badge sheet (C8)


BADGES: dict[Badge, BadgeInfo] = {
    Badge.FIRST_NOTCH: BadgeInfo("First Notch", "Be one of the first customers at a new place."),
    Badge.FOUNDING_MEMBER: BadgeInfo(
        "Founding Member", f"One of the first {MAX_FOUNDING_MEMBER_NO} on Notchs."
    ),
    Badge.CAFE_HOPPER: BadgeInfo("Cafe Hopper", f"Notch {CAFE_HOPPER_CAFES} different cafés."),
    Badge.NIGHT_OWL: BadgeInfo("Night Owl", f"{NIGHT_OWL_VISITS} visits after 9 PM."),
    Badge.REGULAR: BadgeInfo("Regular", f"{REGULAR_VISITS} visits to one place in a month."),
}


def is_night(hour: int) -> bool:
    return hour >= NIGHT_STARTS_HOUR or hour < NIGHT_ENDS_HOUR


_NIGHT = or_(Notch.local_hour >= NIGHT_STARTS_HOUR, Notch.local_hour < NIGHT_ENDS_HOUR)
_COUNTED = Notch.status.in_(COUNTED_STATUSES)


async def venue_customer_count(session: AsyncSession, venue_id: uuid.UUID) -> int:
    """Different customers who have notched here (deleted customers no longer count)."""
    return (
        await session.scalar(
            select(func.count(distinct(Notch.user_id))).where(
                Notch.venue_id == venue_id, _COUNTED, Notch.user_id.is_not(None)
            )
        )
        or 0
    )


async def first_notch_spots_left(session: AsyncSession, venue: Venue) -> int:
    return max(0, venue.first_notch_spots - await venue_customer_count(session, venue.id))


async def visits_in_month(
    session: AsyncSession, user_id: uuid.UUID, venue_id: uuid.UUID, day: date
) -> int:
    month = month_of(day)
    return (
        await session.scalar(
            select(func.count()).where(
                Notch.user_id == user_id,
                Notch.venue_id == venue_id,
                _COUNTED,
                Notch.local_date.between(month.start, month.end),
            )
        )
        or 0
    )


async def night_visits(session: AsyncSession, user_id: uuid.UUID) -> int:
    return (
        await session.scalar(select(func.count()).where(Notch.user_id == user_id, _COUNTED, _NIGHT))
        or 0
    )


async def cafes_notched(session: AsyncSession, user_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count(distinct(Notch.venue_id))).where(
                Notch.user_id == user_id, _COUNTED, Notch.category == VenueCategory.CAFE
            )
        )
        or 0
    )


async def best_month_visits(session: AsyncSession, user_id: uuid.UUID, day: date) -> int:
    """Most Notchs at any one place this month: progress towards Regular."""
    month = month_of(day)
    per_venue = (
        select(func.count().label("n"))
        .where(
            Notch.user_id == user_id,
            _COUNTED,
            Notch.local_date.between(month.start, month.end),
        )
        .group_by(Notch.venue_id)
        .subquery()
    )
    return await session.scalar(select(func.max(per_venue.c.n))) or 0


async def earned(session: AsyncSession, user_id: uuid.UUID) -> dict[Badge, UserBadge]:
    rows = await session.scalars(select(UserBadge).where(UserBadge.user_id == user_id))
    return {Badge(row.badge): row for row in rows}


async def award(
    session: AsyncSession,
    user_id: uuid.UUID,
    badge: Badge,
    *,
    at: datetime,
    venue_id: uuid.UUID | None = None,
    notch_id: uuid.UUID | None = None,
) -> bool:
    """Give the badge if the customer does not have it yet. True if it is new."""
    new_id = await session.scalar(
        insert(UserBadge)
        .values(
            id=new_uuid(),
            user_id=user_id,
            badge=badge,
            venue_id=venue_id,
            notch_id=notch_id,
            earned_at=at,
        )
        .on_conflict_do_nothing(constraint="uq_user_badges_user_id_badge")
        .returning(UserBadge.id)
    )
    return new_id is not None


async def award_after_notch(
    session: AsyncSession, user: User, venue: Venue, notch: Notch
) -> list[Badge]:
    """Everything this Notch earned, in the order the bot announces them."""
    have = set(await earned(session, user.id))
    new: list[Badge] = []

    async def give(badge: Badge, *, at_venue: bool) -> None:
        if await award(
            session,
            user.id,
            badge,
            at=notch.scanned_at,
            venue_id=venue.id if at_venue else None,
            notch_id=notch.id,
        ):
            new.append(badge)

    if Badge.FOUNDING_MEMBER not in have and user.founding_member_no is not None:
        # Announced by its own message (B8), so it is awarded but not listed as "new".
        await award(session, user.id, Badge.FOUNDING_MEMBER, at=notch.scanned_at)
    if (
        Badge.FIRST_NOTCH not in have
        and notch.user_visit_no == 1
        and await venue_customer_count(session, venue.id) <= venue.first_notch_spots
    ):
        await give(Badge.FIRST_NOTCH, at_venue=True)
    if (
        Badge.REGULAR not in have
        and await visits_in_month(session, user.id, venue.id, notch.local_date) >= REGULAR_VISITS
    ):
        await give(Badge.REGULAR, at_venue=True)
    if (
        Badge.NIGHT_OWL not in have
        and is_night(notch.local_hour)
        and await night_visits(session, user.id) >= NIGHT_OWL_VISITS
    ):
        await give(Badge.NIGHT_OWL, at_venue=False)
    if (
        Badge.CAFE_HOPPER not in have
        and notch.category == VenueCategory.CAFE
        and await cafes_notched(session, user.id) >= CAFE_HOPPER_CAFES
    ):
        await give(Badge.CAFE_HOPPER, at_venue=False)
    return new
