"""Reads for the admin panel. Search terms are bound parameters, never pasted into SQL."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import Area, City
from app.modules.scans.models import PointsEntry, ScanSession, ScanStatus, Visit, VisitStatus
from app.modules.users.models import User
from app.modules.venue_applications.models import ApplicationStatus, VenueApplication
from app.modules.venues.models import CounterDevice, Venue, VenueStatus


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _page(
    session: AsyncSession, query: Select[Any], offset: int, limit: int
) -> tuple[Sequence[Row[Any]], int]:
    total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = (await session.execute(query.offset(offset).limit(limit))).all()
    return rows, total or 0


class AdminPanelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count(self, query: Select[Any]) -> int:
        return await self.session.scalar(query) or 0

    async def overview_counts(
        self, today: date, week_ago: date, today_start: datetime, week_start: datetime
    ) -> dict[str, int]:
        venues = dict(
            (
                await self.session.execute(
                    select(Venue.status, func.count())
                    .where(Venue.deleted_at.is_(None))
                    .group_by(Venue.status)
                )
            ).all()
        )
        valid = Visit.status == VisitStatus.VALID
        return {
            "venues_live": venues.get(VenueStatus.COUNTER_LIVE, 0),
            "venues_signed": venues.get(VenueStatus.SIGNED, 0),
            "venues_paused": venues.get(VenueStatus.PAUSED, 0),
            "applications_pending": await self.count(
                select(func.count())
                .select_from(VenueApplication)
                .where(VenueApplication.status == ApplicationStatus.PENDING)
            ),
            "customers": await self.count(
                select(func.count()).select_from(User).where(User.deleted_at.is_(None))
            ),
            "customers_new_7d": await self.count(
                select(func.count())
                .select_from(User)
                .where(User.deleted_at.is_(None), User.created_at >= week_start)
            ),
            "notchs_today": await self.count(
                select(func.count()).select_from(Visit).where(valid, Visit.visit_date == today)
            ),
            "notchs_7d": await self.count(
                select(func.count()).select_from(Visit).where(valid, Visit.visit_date >= week_ago)
            ),
            "scans_refused_today": await self.count(
                select(func.count())
                .select_from(ScanSession)
                .where(
                    ScanSession.status == ScanStatus.REJECTED,
                    ScanSession.created_at >= today_start,
                )
            ),
        }

    async def venues(
        self,
        *,
        status: str | None,
        q: str | None,
        week_ago: date,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Row[Any]], int]:
        counts = (
            select(
                Visit.venue_id,
                func.count().label("total"),
                func.count().filter(Visit.visit_date >= week_ago).label("week"),
            )
            .where(Visit.status == VisitStatus.VALID)
            .group_by(Visit.venue_id)
            .subquery()
        )
        query = (
            select(
                Venue,
                Area.name.label("area"),
                CounterDevice.token_hash.is_not(None).label("paired"),
                CounterDevice.last_seen_at,
                func.coalesce(counts.c.total, 0).label("notchs_total"),
                func.coalesce(counts.c.week, 0).label("notchs_7d"),
            )
            .outerjoin(Area, Area.id == Venue.area_id)
            .outerjoin(
                CounterDevice,
                (CounterDevice.venue_id == Venue.id) & CounterDevice.revoked_at.is_(None),
            )
            .outerjoin(counts, counts.c.venue_id == Venue.id)
            .where(Venue.deleted_at.is_(None))
            .order_by(Venue.created_at.desc(), Venue.id)
        )
        if status:
            query = query.where(Venue.status == status)
        if q:
            query = query.where(or_(Venue.name.ilike(_like(q)), Venue.venue_code.ilike(_like(q))))
        return await _page(self.session, query, offset, limit)

    async def venue_by_code(self, venue_code: str, *, for_update: bool = False) -> Venue | None:
        query = select(Venue).where(Venue.venue_code == venue_code, Venue.deleted_at.is_(None))
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def paired_counter(self, venue_id: uuid.UUID) -> CounterDevice | None:
        return await self.session.scalar(
            select(CounterDevice).where(
                CounterDevice.venue_id == venue_id,
                CounterDevice.revoked_at.is_(None),
                CounterDevice.token_hash.is_not(None),
            )
        )

    @staticmethod
    def _points() -> Any:
        return (
            select(PointsEntry.visit_id, func.sum(PointsEntry.points).label("points"))
            .where(PointsEntry.visit_id.is_not(None))
            .group_by(PointsEntry.visit_id)
            .subquery()
        )

    async def notchs(
        self,
        *,
        venue_code: str | None,
        status: str | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Row[Any]], int]:
        points = AdminPanelRepository._points()
        query = (
            select(
                Visit,
                Venue.venue_code,
                Venue.name.label("venue_name"),
                User.handle,
                func.coalesce(points.c.points, 0).label("points"),
            )
            .join(Venue, Venue.id == Visit.venue_id)
            # A deleted customer's visits stay, with no handle.
            .outerjoin(User, (User.id == Visit.user_id) & User.deleted_at.is_(None))
            .outerjoin(points, points.c.visit_id == Visit.id)
            .order_by(Visit.visited_at.desc(), Visit.id)
        )
        if venue_code:
            query = query.where(Venue.venue_code == venue_code)
        if status:
            query = query.where(Visit.status == status)
        return await _page(self.session, query, offset, limit)

    async def notch(self, venue_id: uuid.UUID, notch_no: int) -> Visit | None:
        return await self.session.scalar(
            select(Visit)
            .where(Visit.venue_id == venue_id, Visit.notch_no == notch_no)
            .with_for_update()
        )

    async def visit_points(self, visit_id: uuid.UUID) -> int:
        return (
            await self.session.scalar(
                select(func.coalesce(func.sum(PointsEntry.points), 0)).where(
                    PointsEntry.visit_id == visit_id
                )
            )
            or 0
        )

    async def customers(
        self, *, status: str | None, q: str | None, offset: int, limit: int
    ) -> tuple[Sequence[Row[Any]], int]:
        counts = (
            select(Visit.user_id, func.count().label("notchs"))
            .where(Visit.status == VisitStatus.VALID)
            .group_by(Visit.user_id)
            .subquery()
        )
        query = (
            select(User, City.name.label("city"), func.coalesce(counts.c.notchs, 0).label("notchs"))
            .outerjoin(City, City.id == User.city_id)
            .outerjoin(counts, counts.c.user_id == User.id)
            .where(User.deleted_at.is_(None))
            .order_by(User.created_at.desc(), User.id)
        )
        if status:
            query = query.where(User.status == status)
        if q:
            query = query.where(or_(User.handle.ilike(_like(q)), User.name.ilike(_like(q))))
        return await _page(self.session, query, offset, limit)

    async def customer_by_handle(self, handle: str) -> User | None:
        return await self.session.scalar(
            select(User).where(User.handle == handle, User.deleted_at.is_(None)).with_for_update()
        )
