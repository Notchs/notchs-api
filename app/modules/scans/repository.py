"""Database reads and locks for the scan pipeline."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scans.models import COUNTED_STATUSES, PointsEntry, ScanSession, Visit
from app.modules.users.models import User
from app.modules.venues.models import CounterDevice, Venue


class ScanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def session_for_message(self, message_id: uuid.UUID) -> ScanSession | None:
        return await self.session.scalar(
            select(ScanSession).where(ScanSession.inbound_message_id == message_id)
        )

    async def visit_for_session(self, scan_session_id: uuid.UUID) -> Visit | None:
        return await self.session.scalar(
            select(Visit).where(Visit.scan_session_id == scan_session_id)
        )

    async def points_for_visit(self, visit_id: uuid.UUID) -> Sequence[PointsEntry]:
        result = await self.session.scalars(
            select(PointsEntry).where(PointsEntry.visit_id == visit_id)
        )
        return result.all()

    async def lock_customer(self, user_id: uuid.UUID) -> User:
        """Lock order is always customer, then venue, so two scans can never deadlock."""
        user = await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert user is not None  # FK target of the message being handled
        return user

    async def lock_venue(self, venue_id: uuid.UUID) -> None:
        await self.session.execute(select(Venue.id).where(Venue.id == venue_id).with_for_update())

    async def venue_by_scan_code(self, scan_code: str) -> Venue | None:
        return await self.session.scalar(select(Venue).where(Venue.scan_code == scan_code))

    async def paired_counter(self, venue_id: uuid.UUID) -> CounterDevice | None:
        return await self.session.scalar(
            select(CounterDevice).where(
                CounterDevice.venue_id == venue_id,
                CounterDevice.revoked_at.is_(None),
                CounterDevice.is_active.is_(True),
                CounterDevice.token_hash.is_not(None),
            )
        )

    async def has_visit_on(self, user_id: uuid.UUID, venue_id: uuid.UUID, day: date) -> bool:
        """Revoked visits count too: the table allows one row per customer, venue and day."""
        found = await self.session.scalar(
            select(Visit.id).where(
                Visit.user_id == user_id, Visit.venue_id == venue_id, Visit.visit_date == day
            )
        )
        return found is not None

    async def count_valid_visits_on(self, user_id: uuid.UUID, day: date) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(Visit)
                .where(
                    Visit.user_id == user_id,
                    Visit.visit_date == day,
                    Visit.status.in_(COUNTED_STATUSES),
                )
            )
            or 0
        )

    async def count_code_uses(self, venue_id: uuid.UUID, slot: int, since: datetime) -> int:
        """Visits at this venue from one live code. `since` keeps the query on the venue/time
        index: a code stops working minutes after it was shown."""
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(Visit)
                .where(
                    Visit.venue_id == venue_id,
                    Visit.visited_at >= since,
                    Visit.code_slot == slot,
                )
            )
            or 0
        )

    async def count_valid_visits_at(self, user_id: uuid.UUID, venue_id: uuid.UUID) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(Visit)
                .where(
                    Visit.user_id == user_id,
                    Visit.venue_id == venue_id,
                    Visit.status.in_(COUNTED_STATUSES),
                )
            )
            or 0
        )

    async def last_notch_no(self, venue_id: uuid.UUID) -> int:
        """Revoked visits keep their number, so numbers are never reused."""
        return (
            await self.session.scalar(
                select(func.max(Visit.notch_no)).where(Visit.venue_id == venue_id)
            )
            or 0
        )
