"""Leaderboards (C7, Home's "#38 in Ahmedabad") and venue rankings (A6, A7), straight from
counted Notchs with SQL. At pilot size (a few thousand customers) this is fast enough; add a
Redis cache in front if it ever is not.

    customer rank   by Notchs in the period, ties to whoever got there first; shown from
                    RANK_FROM_NOTCHES Notchs, "Top X%" only with TOP_PERCENT_MIN_ACTIVE
                    customers active in the period (Page Inventory C2, C7; QR spec)
    venue ranking   by counted Notchs (verified visits) in a week, never paid placement
    trending        biggest growth: the last 7 days against the 7 before
"""

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import Area
from app.modules.locations.time import Period, last_days
from app.modules.notches.models import COUNTED_STATUSES, Notch
from app.modules.users.display import public_name
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import Venue, VenueStatus

RANK_FROM_NOTCHES = 3
TOP_PERCENT_MIN_ACTIVE = 200
LEADERBOARD_SIZE = 10
TRENDING_MIN_VISITS = 3  # a place needs some real visits before it can trend


@dataclass(frozen=True, slots=True)
class Standing:
    rank: int
    notches: int
    active_users: int

    @property
    def top_percent(self) -> int | None:
        """The "Top 8%" line: only for ranked customers, and only once the city is big enough."""
        if self.notches < RANK_FROM_NOTCHES or self.active_users < TOP_PERCENT_MIN_ACTIVE:
            return None
        return max(1, math.ceil(self.rank / self.active_users * 100))


@dataclass(frozen=True, slots=True)
class LeaderRow:
    rank: int
    user_id: uuid.UUID
    name: str  # "Riya S." or "Hidden explorer"
    area: str | None
    notches: int


def _counted(city_id: uuid.UUID, period: Period | None) -> list:
    """Counted Notchs of active customers: blocked accounts, and closed ones waiting for the
    wipe, never appear on a leaderboard or count towards "Top X%"."""
    conditions = [
        Notch.city_id == city_id,
        Notch.status.in_(COUNTED_STATUSES),
        Notch.user_id.in_(select(User.id).where(User.status == UserStatus.ACTIVE)),
    ]
    if period is not None:
        conditions.append(Notch.local_date.between(period.start, period.end))
    return conditions


def _ranked(
    city_id: uuid.UUID,
    period: Period | None,
    *,
    categories: Sequence[str] | None = None,
    area_id: uuid.UUID | None = None,
):
    conditions = _counted(city_id, period)
    if categories:
        conditions.append(Notch.category.in_(categories))
    if area_id is not None:
        conditions.append(Notch.area_id == area_id)
    counts = (
        select(
            Notch.user_id.label("user_id"),
            func.count().label("notches"),
            func.max(Notch.scanned_at).label("last_at"),
        )
        .where(*conditions)
        .group_by(Notch.user_id)
        .subquery()
    )
    return select(
        counts.c.user_id,
        counts.c.notches,
        func.row_number()
        .over(order_by=(counts.c.notches.desc(), counts.c.last_at.asc(), counts.c.user_id))
        .label("rank"),
    ).subquery()


async def standing(
    session: AsyncSession,
    user_id: uuid.UUID,
    city_id: uuid.UUID,
    period: Period | None,
    *,
    categories: Sequence[str] | None = None,
    area_id: uuid.UUID | None = None,
) -> Standing | None:
    """The customer's place in the city for the period, or None if they have no Notchs in it."""
    ranked = _ranked(city_id, period, categories=categories, area_id=area_id)
    row = (
        await session.execute(
            select(
                ranked.c.rank,
                ranked.c.notches,
                select(func.count()).select_from(ranked).scalar_subquery(),
            ).where(ranked.c.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return Standing(rank=row[0], notches=row[1], active_users=row[2])


async def active_users(session: AsyncSession, city_id: uuid.UUID, period: Period | None) -> int:
    return (
        await session.scalar(
            select(func.count(func.distinct(Notch.user_id))).where(*_counted(city_id, period))
        )
        or 0
    )


async def top(
    session: AsyncSession,
    city_id: uuid.UUID,
    period: Period | None,
    *,
    limit: int = LEADERBOARD_SIZE,
    categories: Sequence[str] | None = None,
    area_id: uuid.UUID | None = None,
) -> list[LeaderRow]:
    ranked = _ranked(city_id, period, categories=categories, area_id=area_id)
    rows = await session.execute(
        select(
            ranked.c.rank, ranked.c.user_id, ranked.c.notches, User.name, User.is_public, Area.name
        )
        .join(User, User.id == ranked.c.user_id)
        .outerjoin(Area, Area.id == User.area_id)
        .order_by(ranked.c.rank)
        .limit(limit)
    )
    return [
        LeaderRow(
            rank=rank,
            user_id=user_id,
            name=public_name(name, is_public),
            area=area,
            notches=notches,
        )
        for rank, user_id, notches, name, is_public, area in rows
    ]


@dataclass(frozen=True, slots=True)
class VenueCount:
    venue: Venue
    visits: int
    previous_visits: int = 0


def _live_venues(city_id: uuid.UUID) -> list:
    return [
        Venue.city_id == city_id,
        Venue.status == VenueStatus.COUNTER_LIVE,
        Venue.deleted_at.is_(None),
    ]


def _visits_between(start: date, end: date):
    return (
        select(Notch.venue_id.label("venue_id"), func.count().label("visits"))
        .where(Notch.status.in_(COUNTED_STATUSES), Notch.local_date.between(start, end))
        .group_by(Notch.venue_id)
        .subquery()
    )


def _venue_counts(
    city_id: uuid.UUID,
    current: Period,
    previous: Period,
    categories: Sequence[str] | None,
    area_id: uuid.UUID | None,
) -> Select:
    now_counts = _visits_between(current.start, current.end)
    before = _visits_between(previous.start, previous.end)
    query = (
        select(
            Venue,
            func.coalesce(now_counts.c.visits, 0).label("visits"),
            func.coalesce(before.c.visits, 0).label("previous"),
        )
        .outerjoin(now_counts, now_counts.c.venue_id == Venue.id)
        .outerjoin(before, before.c.venue_id == Venue.id)
        .where(*_live_venues(city_id))
    )
    if categories:
        query = query.where(Venue.category.in_(categories))
    if area_id is not None:
        query = query.where(Venue.area_id == area_id)
    return query


async def venue_ranking(
    session: AsyncSession,
    city_id: uuid.UUID,
    week: Period,
    *,
    categories: Sequence[str] | None = None,
    area_id: uuid.UUID | None = None,
    trending_first: bool = False,
    limit: int = 50,
) -> list[VenueCount]:
    """Live venues by verified visits in `week` (A7). Venues with no visits yet are listed last
    so a new place is still findable. `trending_first` sorts by growth on the week before."""
    previous = Period(week.start - timedelta(days=7), week.end - timedelta(days=7))
    query = _venue_counts(city_id, week, previous, categories, area_id)
    visits, prev = query.selected_columns.visits, query.selected_columns.previous
    order = (
        ((visits - prev).desc(), visits.desc(), Venue.name)
        if trending_first
        else (visits.desc(), Venue.name)
    )
    rows = await session.execute(query.order_by(*order).limit(limit))
    return [VenueCount(venue, visits, previous) for venue, visits, previous in rows]


async def trending(
    session: AsyncSession,
    city_id: uuid.UUID,
    today: date,
    *,
    limit: int = 3,
    categories: Sequence[str] | None = None,
) -> list[VenueCount]:
    """A6's "Trending this week": fastest growth over the last 7 days, with real visits only."""
    current = last_days(today, 7)
    previous = last_days(today - timedelta(days=7), 7)
    query = _venue_counts(city_id, current, previous, categories, None)
    visits, prev = query.selected_columns.visits, query.selected_columns.previous
    rows = await session.execute(
        query.where(visits >= TRENDING_MIN_VISITS, visits > prev)
        .order_by((visits - prev).desc(), visits.desc(), Venue.name)
        .limit(limit)
    )
    return [VenueCount(venue, visits, previous) for venue, visits, previous in rows]
