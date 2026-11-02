"""City leaderboards: fast ranks in Redis, always rebuilt from `points_ledger` in Postgres.

    lb:<city id>:all        all-time points
    lb:<city id>:<YYYY-MM>  this month's points (India time); old months expire

Points go on the board of the city the visit happened in. A counted visit adds to both boards right
after it commits. If Redis misses one (a crash, a restart, an empty Redis) ranks would be wrong, so
`rebuild` replaces the boards from the ledger: at startup, every hour and from the CLI.

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

from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time import IST, ist_date
from app.core.logging import get_logger
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
        Revoked visits, and blocked or deleted customers, do not count."""
        now = now or datetime.now(UTC)
        city = func.coalesce(Venue.city_id, User.city_id)
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
        boards: dict[str, dict[str, int]] = defaultdict(dict)
        for city_id, user_id, points in await session.execute(query):
            boards[board_key(city_id, ALL_TIME)][str(user_id)] = int(points)
        this_month = month_start(now)
        next_month = (this_month + timedelta(days=32)).replace(day=1)
        monthly = query.where(counted_at >= this_month, counted_at < next_month)
        month = month_of(now)
        for city_id, user_id, points in await session.execute(monthly):
            boards[board_key(city_id, month)][str(user_id)] = int(points)

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
