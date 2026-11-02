"""Venues in the admin panel (G2): list, look at, edit, pause and resume. Changes are audited."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.modules.admin_users.models import AdminUser
from app.modules.audit import service as audit
from app.modules.locations.models import Area, City
from app.modules.locations.time import last_days, today
from app.modules.scans.models import COUNTED_STATUSES, Visit
from app.modules.venues.admin_schemas import AdminVenueOut, AdminVenueRowOut
from app.modules.venues.counters import counter_out
from app.modules.venues.exceptions import VenueNotFoundException
from app.modules.venues.models import CounterDevice, Venue, VenueStatus
from app.modules.venues.repository import VenueRepository
from app.modules.venues.schemas import VenueOut, VenueUpdate
from app.modules.venues.service import VenueService

_CODE = re.compile(r"VEN-[0-9A-Z]{8}")


class VenueAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.venues = VenueService(VenueRepository(session))

    async def list(
        self, params: PaginationParams, *, status: VenueStatus | None, q: str | None
    ) -> tuple[list[AdminVenueRowOut], int]:
        conditions = [Venue.deleted_at.is_(None)]
        if status is not None:
            conditions.append(Venue.status == status)
        if q and q.strip():
            conditions.append(Venue.name.ilike(f"%{q.strip()}%"))
        venues = (
            await self.session.scalars(
                select(Venue)
                .where(*conditions)
                .order_by(Venue.name, Venue.id)
                .offset(params.offset)
                .limit(params.size)
            )
        ).all()
        total = await self.session.scalar(
            select(func.count()).select_from(Venue).where(*conditions)
        )
        ids = [v.id for v in venues]
        counts = await self._counts(ids)
        area_ids = {v.area_id for v in venues if v.area_id}
        areas = (
            dict(
                (
                    await self.session.execute(
                        select(Area.id, Area.name).where(Area.id.in_(area_ids))
                    )
                ).all()
            )
            if area_ids
            else {}
        )
        counters = (
            {
                c.venue_id: c
                for c in await self.session.scalars(
                    select(CounterDevice).where(
                        CounterDevice.venue_id.in_(ids), CounterDevice.revoked_at.is_(None)
                    )
                )
            }
            if ids
            else {}
        )
        now = datetime.now(UTC)
        rows = [
            AdminVenueRowOut(
                id=v.id,
                venue_code=v.venue_code,
                name=v.name,
                area=areas.get(v.area_id) if v.area_id else None,
                category=v.category,
                status=v.status,
                founding_venue_no=v.founding_venue_no,
                notches_7d=counts.get(v.id, 0),
                counter_online=counter_out(counters[v.id], now).online
                if v.id in counters
                else None,
                created_at=v.created_at,
            )
            for v in venues
        ]
        return rows, total or 0

    async def get(self, ref: str) -> AdminVenueOut:
        return await self._detail(await self._venue(ref))

    async def update(self, ref: str, body: VenueUpdate, admin: AdminUser) -> AdminVenueOut:
        venue = await self.venues.update((await self._venue(ref)).id, body)
        await audit.record(
            self.session,
            actor=admin,
            action="venue.updated",
            target_type="venue",
            target_id=venue.id,
            target_label=venue.name,
            details={"fields": sorted(body.model_fields_set)},
        )
        return await self._detail(venue)

    async def set_status(self, ref: str, status: VenueStatus, admin: AdminUser) -> AdminVenueOut:
        venue = await self._venue(ref, for_update=True)
        before = venue.status
        venue = await self.venues.set_status(venue.id, status, admin_id=admin.id)
        if before != status:  # a double click is not a second change
            await audit.record(
                self.session,
                actor=admin,
                action=f"venue.{status}",
                target_type="venue",
                target_id=venue.id,
                target_label=venue.name,
                details={"from": before},
            )
        return await self._detail(venue)

    async def _venue(self, ref: str, *, for_update: bool = False) -> Venue:
        ref = ref.strip().upper()
        if _CODE.fullmatch(ref):
            condition = Venue.venue_code == ref
        else:
            try:
                condition = Venue.id == uuid.UUID(ref)
            except ValueError as exc:
                raise VenueNotFoundException() from exc
        query = select(Venue).where(condition, Venue.deleted_at.is_(None))
        venue = await self.session.scalar(query.with_for_update() if for_update else query)
        if venue is None:
            raise VenueNotFoundException()
        return venue

    async def _counts(self, venue_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not venue_ids:
            return {}
        week = last_days(today(None), 7)
        return dict(
            (
                await self.session.execute(
                    select(Visit.venue_id, func.count())
                    .where(
                        Visit.venue_id.in_(venue_ids),
                        Visit.status.in_(COUNTED_STATUSES),
                        Visit.visit_date.between(week.start, week.end),
                    )
                    .group_by(Visit.venue_id)
                )
            ).all()
        )

    async def _detail(self, venue: Venue) -> AdminVenueOut:
        area = (
            await self.session.scalar(select(Area.name).where(Area.id == venue.area_id))
            if venue.area_id
            else None
        )
        city = await self.session.scalar(select(City.name).where(City.id == venue.city_id))
        counter = await self.session.scalar(
            select(CounterDevice).where(
                CounterDevice.venue_id == venue.id, CounterDevice.revoked_at.is_(None)
            )
        )
        total = await self.session.scalar(
            select(func.count()).where(
                Visit.venue_id == venue.id, Visit.status.in_(COUNTED_STATUSES)
            )
        )
        return AdminVenueOut(
            **VenueOut.model_validate(venue).model_dump(),
            area=area,
            city=city or "",
            notches_7d=(await self._counts([venue.id])).get(venue.id, 0),
            notches_total=total or 0,
            counter=counter_out(counter) if counter else None,
        )
