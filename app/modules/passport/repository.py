"""Data access for the passport: a customer's own visits and points, never anyone else's."""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Row, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import Area
from app.modules.scans.models import PointsEntry, Visit, VisitStatus
from app.modules.venues.models import Venue

_COUNTED = Visit.status == VisitStatus.VALID


class PassportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def totals(self, user_id: uuid.UUID) -> tuple[int, int, int]:
        """Counted Notchs, places, and points: adjustments count, points of removed (revoked)
        Notchs don't, as on the leaderboard."""
        notches, places = (
            await self.session.execute(
                select(func.count(), func.count(distinct(Visit.venue_id))).where(
                    Visit.user_id == user_id, _COUNTED
                )
            )
        ).one()
        points = await self.session.scalar(
            select(func.coalesce(func.sum(PointsEntry.points), 0))
            .outerjoin(Visit, Visit.id == PointsEntry.visit_id)
            .where(
                PointsEntry.user_id == user_id,
                or_(Visit.id.is_(None), Visit.status == VisitStatus.VALID),
            )
        )
        return notches, places, points or 0

    async def notches(
        self, user_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[Row[Any]], int]:
        """Newest first, with the venue, its area and what each visit earned."""
        points = (
            select(PointsEntry.visit_id, func.sum(PointsEntry.points).label("points"))
            .where(PointsEntry.user_id == user_id)
            .group_by(PointsEntry.visit_id)
            .subquery()
        )
        where = (Visit.user_id == user_id, _COUNTED)
        rows = (
            await self.session.execute(
                select(
                    Visit.notch_no,
                    Visit.visit_date,
                    Visit.visited_at,
                    Visit.visit_no_at_venue,
                    Venue.name.label("venue_name"),
                    Venue.category,
                    Area.name.label("venue_area"),
                    func.coalesce(points.c.points, 0).label("points"),
                )
                .join(Venue, Venue.id == Visit.venue_id)
                .outerjoin(Area, Area.id == Venue.area_id)
                .outerjoin(points, points.c.visit_id == Visit.id)
                .where(*where)
                .order_by(Visit.visited_at.desc(), Visit.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        total = await self.session.scalar(select(func.count()).select_from(Visit).where(*where))
        return rows, total or 0

    async def first_visits_between(
        self, user_id: uuid.UUID, category: str, start: date, end: date
    ) -> int:
        """Venues of this niche first visited in [start, end)."""
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(Visit)
                .join(Venue, Venue.id == Visit.venue_id)
                .where(
                    Visit.user_id == user_id,
                    _COUNTED,
                    Visit.visit_no_at_venue == 1,
                    Visit.visit_date >= start,
                    Visit.visit_date < end,
                    Venue.category == category,
                )
            )
            or 0
        )

    async def area_in_city(self, area_id: uuid.UUID, city_id: uuid.UUID | None) -> Area | None:
        query = select(Area).where(Area.id == area_id)
        if city_id is not None:
            query = query.where(Area.city_id == city_id)
        return await self.session.scalar(query)
