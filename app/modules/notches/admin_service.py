"""Proof review (G1) and fraud flags (G4) in the admin panel. Every decision is audited."""

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.modules.admin_users.models import AdminUser
from app.modules.audit import service as audit
from app.modules.locations.models import Area, City
from app.modules.locations.time import local
from app.modules.notches.admin_schemas import FraudSignalOut, ReviewNotchDetailOut, ReviewNotchOut
from app.modules.notches.exceptions import (
    NotchReviewNotFoundException,
    SignalNotFoundException,
)
from app.modules.notches.models import FraudSignal, Notch, NotchStatus, SignalStatus
from app.modules.users.models import User
from app.modules.venues.models import Venue

_NOTCH_CODE = re.compile(r"NCH-[0-9A-Z]{8}")


class NotchReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- G1: proof review ----------------------------------------------------------------

    async def queue(
        self,
        params: PaginationParams,
        *,
        status: NotchStatus | None = NotchStatus.FLAGGED,
        venue_id: uuid.UUID | None = None,
    ) -> tuple[list[ReviewNotchOut], int]:
        """Flagged Notchs oldest first, so the queue is worked through in order (A / R keys)."""
        conditions = []
        if status is not None:
            conditions.append(Notch.status == status)
        if venue_id is not None:
            conditions.append(Notch.venue_id == venue_id)
        order = Notch.scanned_at.asc() if status == NotchStatus.FLAGGED else Notch.scanned_at.desc()
        notches = (
            await self.session.scalars(
                select(Notch)
                .where(*conditions)
                .order_by(order, Notch.id)
                .offset(params.offset)
                .limit(params.size)
            )
        ).all()
        total = await self.session.scalar(
            select(func.count()).select_from(Notch).where(*conditions)
        )
        return await self._outs(notches), total or 0

    async def get(self, ref: str) -> ReviewNotchDetailOut:
        notch = await self._notch(ref)
        (out,) = await self._outs([notch])
        same = []
        if notch.counter_id is not None:
            same = (
                await self.session.scalars(
                    select(Notch)
                    .where(
                        Notch.counter_id == notch.counter_id,
                        Notch.code_window == notch.code_window,
                        Notch.id != notch.id,
                    )
                    .order_by(Notch.scanned_at)
                )
            ).all()
        return ReviewNotchDetailOut(**out.model_dump(), same_code=await self._outs(same))

    async def decide(
        self, ref: str, admin: AdminUser, *, approve: bool, note: str | None
    ) -> ReviewNotchOut:
        notch = await self._notch(ref, for_update=True)
        before = notch.status
        notch.status = NotchStatus.VERIFIED if approve else NotchStatus.REJECTED
        notch.reviewed_by_admin_id = admin.id
        notch.reviewed_at = datetime.now(UTC)
        notch.review_note = note
        await self.session.flush()
        venue_name = await self.session.scalar(select(Venue.name).where(Venue.id == notch.venue_id))
        await audit.record(
            self.session,
            actor=admin,
            action="notch.approved" if approve else "notch.rejected",
            target_type="notch",
            target_id=notch.id,
            target_label=f"{notch.notch_code} · {venue_name}",
            details={"from": before, "note": note} if note else {"from": before},
        )
        (out,) = await self._outs([notch])
        return out

    async def _notch(self, ref: str, *, for_update: bool = False) -> Notch:
        ref = ref.strip().upper()
        if _NOTCH_CODE.fullmatch(ref):
            condition = Notch.notch_code == ref
        else:
            try:
                condition = Notch.id == uuid.UUID(ref)
            except ValueError as exc:
                raise NotchReviewNotFoundException() from exc
        query = select(Notch).where(condition)
        notch = await self.session.scalar(query.with_for_update() if for_update else query)
        if notch is None:
            raise NotchReviewNotFoundException()
        return notch

    async def _outs(self, notches: Sequence[Notch]) -> list[ReviewNotchOut]:
        if not notches:
            return []
        venues = {
            v.id: v
            for v in await self.session.scalars(
                select(Venue).where(Venue.id.in_({n.venue_id for n in notches}))
            )
        }
        tz = dict(
            (
                await self.session.execute(
                    select(City.id, City.timezone).where(
                        City.id.in_({v.city_id for v in venues.values()})
                    )
                )
            ).all()
        )
        area_ids = {n.area_id for n in notches if n.area_id}
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
        user_ids = {n.user_id for n in notches if n.user_id}
        codes = (
            dict(
                (
                    await self.session.execute(
                        select(User.id, User.user_code).where(User.id.in_(user_ids))
                    )
                ).all()
            )
            if user_ids
            else {}
        )
        admin_ids = {n.reviewed_by_admin_id for n in notches if n.reviewed_by_admin_id}
        admins = (
            dict(
                (
                    await self.session.execute(
                        select(AdminUser.id, AdminUser.name).where(AdminUser.id.in_(admin_ids))
                    )
                ).all()
            )
            if admin_ids
            else {}
        )
        out = []
        for n in notches:
            venue = venues[n.venue_id]
            out.append(
                ReviewNotchOut(
                    id=n.id,
                    notch_code=n.notch_code,
                    status=n.status,
                    venue_id=venue.id,
                    venue_name=venue.name,
                    area=areas.get(n.area_id) if n.area_id else None,
                    customer_code=codes.get(n.user_id) if n.user_id else None,
                    scanned_at=n.scanned_at,
                    local_date=n.local_date,
                    time=local(n.scanned_at, tz.get(venue.city_id)).strftime("%H:%M"),
                    code=n.code,
                    code_offset=n.code_offset,
                    notch_no=n.notch_no,
                    flags=list(n.flags),
                    flag_note=n.flag_note,
                    reviewed_by=admins.get(n.reviewed_by_admin_id)
                    if n.reviewed_by_admin_id
                    else None,
                    reviewed_at=n.reviewed_at,
                    review_note=n.review_note,
                )
            )
        return out

    # --- G4: fraud flags -----------------------------------------------------------------

    async def signals(
        self, params: PaginationParams, *, status: SignalStatus | None = SignalStatus.OPEN
    ) -> tuple[list[FraudSignalOut], int]:
        conditions = [FraudSignal.status == status] if status else []
        rows = (
            await self.session.scalars(
                select(FraudSignal)
                .where(*conditions)
                .order_by(FraudSignal.last_seen_at.desc(), FraudSignal.id)
                .offset(params.offset)
                .limit(params.size)
            )
        ).all()
        total = await self.session.scalar(
            select(func.count()).select_from(FraudSignal).where(*conditions)
        )
        return await self._signal_outs(rows), total or 0

    async def resolve(
        self, signal_id: uuid.UUID, admin: AdminUser, status: SignalStatus, note: str | None
    ) -> FraudSignalOut:
        signal = await self.session.get(FraudSignal, signal_id, with_for_update=True)
        if signal is None:
            raise SignalNotFoundException()
        signal.status = status
        signal.resolved_by_admin_id = admin.id
        signal.resolved_at = datetime.now(UTC)
        signal.resolution_note = note
        await self.session.flush()
        (out,) = await self._signal_outs([signal])
        await audit.record(
            self.session,
            actor=admin,
            action=f"fraud_signal.{status}",
            target_type="fraud_signal",
            target_id=signal.id,
            target_label=f"{signal.kind} · {out.venue_name}",
            details={"note": note} if note else {},
        )
        return out

    async def _signal_outs(self, signals: Sequence[FraudSignal]) -> list[FraudSignalOut]:
        if not signals:
            return []
        names = dict(
            (
                await self.session.execute(
                    select(Venue.id, Venue.name).where(Venue.id.in_({s.venue_id for s in signals}))
                )
            ).all()
        )
        admin_ids = {s.resolved_by_admin_id for s in signals if s.resolved_by_admin_id}
        admins = (
            dict(
                (
                    await self.session.execute(
                        select(AdminUser.id, AdminUser.name).where(AdminUser.id.in_(admin_ids))
                    )
                ).all()
            )
            if admin_ids
            else {}
        )
        return [
            FraudSignalOut(
                id=s.id,
                kind=s.kind,
                status=s.status,
                venue_id=s.venue_id,
                venue_name=names.get(s.venue_id, ""),
                counter_id=s.counter_id,
                first_seen_at=s.first_seen_at,
                last_seen_at=s.last_seen_at,
                occurrences=s.occurrences,
                details=s.details,
                resolved_by=admins.get(s.resolved_by_admin_id) if s.resolved_by_admin_id else None,
                resolved_at=s.resolved_at,
                resolution_note=s.resolution_note,
            )
            for s in signals
        ]
