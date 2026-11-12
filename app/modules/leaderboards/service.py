"""City leaderboards: fast ranks in Redis, always rebuilt from `points_ledger` in Postgres.

    lb:<city id>:all        all-time points
    lb:<city id>:<YYYY-MM>  this month's points (India time); old months expire

Points go on the board of the city the visit happened in. Right after a Notch is added or removed,
`sync_user` puts that customer's exact rank points on their boards. If Redis misses one (a crash,
a restart, an empty Redis) ranks would be wrong, so `rebuild` replaces the boards from the ledger:
at startup, every hour and from the CLI.

Rank decay (decay.py): after 15 days without a Notch, rank points drop 10% a week until the next
Notch; the points earned never change. The hourly rebuild applies it.

Everyone with points is ranked. Equal points share a rank. Private customers keep their place, but
the public list never shows who they are. Phone numbers are never on a board.
"""

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time import IST, ist_date
from app.core.logging import get_logger
from app.modules.leaderboards.decay import DECAY_AFTER_DAYS, kept_percent, rank_points
from app.modules.scans.models import PointsEntry, Visit, VisitStatus
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import Venue

logger = get_logger(__name__)

ALL_TIME = "all"
MONTH_TTL_SECONDS = 70 * 24 * 60 * 60
KEY_PATTERN = "lb:*"
DEFAULT_REBUILD_INTERVAL_SECONDS = 60 * 60


class Period(StrEnum):
    MONTH = "month"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class Rank:
    rank: int
    points: int


@dataclass(frozen=True, slots=True)
class Row:
    rank: int
    points: int
    name: str | None
    handle: str | None


def month_of(moment: datetime) -> str:
    return ist_date(moment).strftime("%Y-%m")


def month_start(moment: datetime) -> datetime:
    day = ist_date(moment)
    return datetime(day.year, day.month, 1, tzinfo=IST)


def board_key(city_id: uuid.UUID, period: str) -> str:
    return f"lb:{city_id}:{period}"


def _period_key(city_id: uuid.UUID, period: Period, now: datetime) -> str:
    return board_key(city_id, ALL_TIME if period == Period.ALL else month_of(now))


class LeaderboardService:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def add_points(
        self, city_id: uuid.UUID, user_id: uuid.UUID, points: int, visited_on: date
    ) -> None:
        """Right after a visit commits."""
        month_key = board_key(city_id, visited_on.strftime("%Y-%m"))
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zincrby(board_key(city_id, ALL_TIME), points, str(user_id))
            pipe.zincrby(month_key, points, str(user_id))
            pipe.expire(month_key, MONTH_TTL_SECONDS)
            await pipe.execute()

    async def rank_of(
        self, city_id: uuid.UUID, user_id: uuid.UUID, period: Period, *, now: datetime | None = None
    ) -> Rank | None:
        """None if they have no points on this board."""
        key = _period_key(city_id, period, now or datetime.now(UTC))
        score = await self.redis.zscore(key, str(user_id))
        if score is None:
            return None
        ahead = await self.redis.zcount(key, f"({score}", "+inf")
        return Rank(rank=ahead + 1, points=int(score))

    async def top(
        self,
        session: AsyncSession,
        city_id: uuid.UUID,
        period: Period,
        limit: int,
        *,
        now: datetime | None = None,
    ) -> tuple[list[Row], int]:
        """The top rows and how many people are ranked."""
        key = _period_key(city_id, period, now or datetime.now(UTC))
        entries = await self.redis.zrevrange(key, 0, limit - 1, withscores=True)
        total = await self.redis.zcard(key)
        people = await self._public_people(session, [uuid.UUID(member) for member, _ in entries])

        rows: list[Row] = []
        for position, (member, score) in enumerate(entries, start=1):
            same_as_last = rows and rows[-1].points == int(score)
            rank = rows[-1].rank if same_as_last else position
            name, handle = people.get(uuid.UUID(member), (None, None))
            rows.append(Row(rank=rank, points=int(score), name=name, handle=handle))
        return rows, total

    @staticmethod
    async def _public_people(
        session: AsyncSession, ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[str | None, str]]:
        """Only customers who chose to be public and are still active."""
        if not ids:
            return {}
        rows = await session.execute(
            select(User.id, User.name, User.handle).where(
                User.id.in_(ids),
                User.is_public.is_(True),
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
        )
        return {id_: (name, handle) for id_, name, handle in rows}

    async def rebuild(self, session: AsyncSession, *, now: datetime | None = None) -> int:
        """Replace every board from the ledger, all at once. Returns how many boards were written.
        Revoked visits, and blocked or deleted customers, do not count. Rank decay applies."""
        now = now or datetime.now(UTC)
        query, month_query = _totals(now)
        kept = await _kept_percents(session, ist_date(now))
        boards: dict[str, dict[str, int]] = defaultdict(dict)
        for city_id, user_id, points in await session.execute(query):
            boards[board_key(city_id, ALL_TIME)][str(user_id)] = rank_points(
                int(points), kept.get(user_id, 100)
            )
        month = month_of(now)
        for city_id, user_id, points in await session.execute(month_query):
            boards[board_key(city_id, month)][str(user_id)] = rank_points(
                int(points), kept.get(user_id, 100)
            )

        stale = [key async for key in self.redis.scan_iter(match=KEY_PATTERN)]
        async with self.redis.pipeline(transaction=True) as pipe:
            if stale:
                pipe.delete(*stale)
            for key, scores in boards.items():
                pipe.zadd(key, scores)
                if not key.endswith(f":{ALL_TIME}"):
                    pipe.expire(key, MONTH_TTL_SECONDS)
            await pipe.execute()
        return len(boards)

    async def sync_user(
        self, session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> None:
        """Put one customer's exact rank points on their boards, right after a Notch is added or
        removed (a new Notch also ends any decay). Replaces their scores rather than adding, so
        it is always the same as a rebuild."""
        now = now or datetime.now(UTC)
        query, month_query = _totals(now)
        kept = (await _kept_percents(session, ist_date(now), user_id)).get(user_id, 100)
        all_time = {
            city_id: int(points)
            for city_id, _, points in await session.execute(
                query.where(PointsEntry.user_id == user_id)
            )
        }
        this_month = {
            city_id: int(points)
            for city_id, _, points in await session.execute(
                month_query.where(PointsEntry.user_id == user_id)
            )
        }
        # Every city they ever had points in, so a board they no longer belong on is cleared.
        city = _points_city()
        cities = set(
            (
                await session.scalars(
                    select(city)
                    .distinct()
                    .select_from(PointsEntry)
                    .join(User, User.id == PointsEntry.user_id)
                    .outerjoin(Visit, Visit.id == PointsEntry.visit_id)
                    .outerjoin(Venue, Venue.id == Visit.venue_id)
                    .where(PointsEntry.user_id == user_id, city.is_not(None))
                )
            ).all()
        )
        member, month = str(user_id), month_of(now)
        async with self.redis.pipeline(transaction=True) as pipe:
            for city_id in cities:
                for key, totals in (
                    (board_key(city_id, ALL_TIME), all_time),
                    (board_key(city_id, month), this_month),
                ):
                    if city_id in totals:
                        pipe.zadd(key, {member: rank_points(totals[city_id], kept)})
                    else:
                        pipe.zrem(key, member)
                pipe.expire(board_key(city_id, month), MONTH_TTL_SECONDS)
            await pipe.execute()


def _points_city() -> Any:
    """The city points count for: fixed on the ledger row since 0012. The fallbacks only matter
    for a row a future code path forgets to fill."""
    return func.coalesce(PointsEntry.city_id, Venue.city_id, User.city_id)


def _totals(now: datetime) -> tuple[Select, Select]:
    """(city, customer, points) from the ledger: all time, and this month (India time).
    Revoked visits and blocked or deleted customers don't count."""
    city = _points_city()
    counted_at = func.coalesce(Visit.visited_at, PointsEntry.created_at)
    query = (
        select(city, PointsEntry.user_id, func.sum(PointsEntry.points))
        .join(User, User.id == PointsEntry.user_id)
        .outerjoin(Visit, Visit.id == PointsEntry.visit_id)
        .outerjoin(Venue, Venue.id == Visit.venue_id)
        .where(
            or_(Visit.id.is_(None), Visit.status == VisitStatus.VALID),
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
            city.is_not(None),
        )
        .group_by(city, PointsEntry.user_id)
    )
    this_month = month_start(now)
    next_month = (this_month + timedelta(days=32)).replace(day=1)
    return query, query.where(counted_at >= this_month, counted_at < next_month)


async def last_notch_on(session: AsyncSession, user_id: uuid.UUID) -> date | None:
    """The day (India time) of the customer's latest counted Notch."""
    return await session.scalar(
        select(func.max(Visit.visit_date)).where(
            Visit.user_id == user_id, Visit.status == VisitStatus.VALID
        )
    )


async def _kept_percents(
    session: AsyncSession, today: date, user_id: uuid.UUID | None = None
) -> dict[uuid.UUID, int]:
    """Customers whose rank points are decaying, and how much they keep (decay.py): those
    whose latest counted Notch is more than 15 days old."""
    last = func.max(Visit.visit_date)
    query = (
        select(Visit.user_id, last)
        .where(Visit.status == VisitStatus.VALID)
        .group_by(Visit.user_id)
        .having(last < today - timedelta(days=DECAY_AFTER_DAYS))
    )
    if user_id is not None:
        query = query.where(Visit.user_id == user_id)
    return {member: kept_percent(last, today) for member, last in await session.execute(query)}


async def run_rebuilder(
    session_factory: Callable[[], AsyncSession], redis: Redis, interval_seconds: float
) -> None:
    """Background loop started with the app: rebuild now, then every `interval_seconds`."""
    while True:
        try:
            async with session_factory() as session:
                boards = await LeaderboardService(redis).rebuild(session)
            logger.info("Leaderboards rebuilt: %d board(s)", boards)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Leaderboard rebuild failed; trying again next round")
        await asyncio.sleep(interval_seconds)
