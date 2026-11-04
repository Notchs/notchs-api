"""Reads for one venue's insights. Every query is filtered by the venue's id."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scans.models import ScanSession, Visit, VisitStatus
from app.modules.users.models import User
from app.modules.venues.models import CounterDevice


class InsightsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def guest_numbers(self, venue_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Guest #1, #2... in the order people were first notched here. Revoked visits count,
        so a number never moves to someone else."""
        first = func.min(Visit.visited_at)
        rows = await self.session.execute(
            select(Visit.user_id)
            .where(Visit.venue_id == venue_id)
            .group_by(Visit.user_id)
            .order_by(first, Visit.user_id)
        )
        return {user_id: n for n, (user_id,) in enumerate(rows, start=1)}

    async def visits(
        self, venue_id: uuid.UUID, since: datetime | None = None
    ) -> Sequence[Row[Any]]:
        """Counted visits, oldest first: who, when, and their Nth visit here."""
        query = select(Visit.user_id, Visit.visited_at, Visit.visit_no_at_venue).where(
            Visit.venue_id == venue_id, Visit.status == VisitStatus.VALID
        )
        if since is not None:
            query = query.where(Visit.visited_at >= since)
        return (await self.session.execute(query.order_by(Visit.visited_at))).all()

    async def count_visits(self, venue_id: uuid.UUID) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(Visit)
                .where(Visit.venue_id == venue_id, Visit.status == VisitStatus.VALID)
            )
            or 0
        )

    async def last_visit_at(self, venue_id: uuid.UUID) -> datetime | None:
        return await self.session.scalar(
            select(func.max(Visit.visited_at)).where(
                Visit.venue_id == venue_id, Visit.status == VisitStatus.VALID
            )
        )

    async def scans(
        self, venue_id: uuid.UUID, since: datetime, limit: int | None = None
    ) -> Sequence[Row[Any]]:
        """Scan attempts here, newest first, with the Notch they became (if any)."""
        query = (
            select(
                ScanSession.created_at,
                ScanSession.status,
                ScanSession.reject_reason,
                ScanSession.code_submitted,
                ScanSession.user_id,
                Visit.notch_no,
                CounterDevice.label.label("counter_name"),
            )
            .outerjoin(Visit, Visit.scan_session_id == ScanSession.id)
            .outerjoin(CounterDevice, CounterDevice.id == ScanSession.counter_device_id)
            .where(ScanSession.venue_id == venue_id, ScanSession.created_at >= since)
            .order_by(ScanSession.created_at.desc(), ScanSession.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return (await self.session.execute(query)).all()

    async def shared_instagram(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Instagram usernames of these customers, only for those who chose to show it."""
        if not user_ids:
            return {}
        rows = await self.session.execute(
            select(User.id, User.instagram_handle).where(
                User.id.in_(user_ids),
                User.show_instagram_to_venues.is_(True),
                User.instagram_handle.is_not(None),
                User.deleted_at.is_(None),
            )
        )
        return {user_id: handle for user_id, handle in rows}

    async def counter(self, venue_id: uuid.UUID) -> CounterDevice | None:
        return await self.session.scalar(
            select(CounterDevice).where(
                CounterDevice.venue_id == venue_id, CounterDevice.revoked_at.is_(None)
            )
        )
