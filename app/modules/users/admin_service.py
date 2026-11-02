"""Customers in the admin panel (G3): search, look up (audited), block, delete, and the record
of deletion requests. Phone numbers only ever leave here masked."""

import contextlib
import re
import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.common.phone import normalize_phone
from app.modules.admin_users.models import AdminUser
from app.modules.audit import service as audit
from app.modules.auth.enums import SubjectType
from app.modules.auth.sessions import SessionService, Subject
from app.modules.locations.models import Area
from app.modules.notches.models import Notch, UserBadge
from app.modules.users.admin_schemas import (
    AdminUserOut,
    AdminUserRowOut,
    AdminVisitOut,
    DeletionOut,
)
from app.modules.users.deletion import DeletionService
from app.modules.users.exceptions import UserDeletedException, UserNotFoundException
from app.modules.users.models import (
    DeletionSource,
    DeletionStatus,
    User,
    UserDeletion,
    UserStatus,
)
from app.modules.venues.models import Venue

_CODE = re.compile(r"USR-[0-9A-Z]{8}")
RECENT_VISITS = 50


def masked(phone: str) -> str:
    """+919876543210 -> "•••• 3210"."""
    return f"•••• {phone[-4:]}"


def row_out(user: User) -> AdminUserRowOut:
    return AdminUserRowOut(
        id=user.id,
        user_code=user.user_code,
        phone=masked(user.phone_e164),
        name=user.name,
        status=user.status,
        founding_member_no=user.founding_member_no,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
    )


def deletion_out(row: UserDeletion) -> DeletionOut:
    return DeletionOut(
        id=row.id,
        user_code=row.user_code,
        phone=f"•••• {row.phone_last4}",
        source=row.source,
        status=row.status,
        requested_at=row.created_at,
        confirmed_at=row.confirmed_at,
        completed_at=row.completed_at,
    )


class UserAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self, params: PaginationParams, q: str | None
    ) -> tuple[list[AdminUserRowOut], int]:
        """By full phone number (any common format), USR- code, or part of a name."""
        conditions = []
        if q and q.strip():
            term = q.strip()
            options = [User.user_code == term.upper(), User.name.ilike(f"%{term}%")]
            with contextlib.suppress(ValueError):  # not a phone number: code and name only
                options.append(User.phone_e164 == normalize_phone(term))
            conditions.append(or_(*options))
        users = (
            await self.session.scalars(
                select(User)
                .where(*conditions)
                .order_by(User.created_at.desc(), User.id)
                .offset(params.offset)
                .limit(params.size)
            )
        ).all()
        total = await self.session.scalar(select(func.count()).select_from(User).where(*conditions))
        return [row_out(u) for u in users], total or 0

    async def look_up(self, ref: str, admin: AdminUser) -> AdminUserOut:
        """One customer and their latest visits. Every look-up is in the audit log."""
        user = await self._user(ref)
        await audit.record(
            self.session,
            actor=admin,
            action="user.looked_up",
            target_type="user",
            target_id=user.id,
            target_label=user.user_code,
        )
        return await self._detail(user)

    async def set_status(self, ref: str, admin: AdminUser, status: UserStatus) -> AdminUserOut:
        user = await self._user(ref, for_update=True)
        if user.status == UserStatus.DELETED:
            raise UserDeletedException()
        changed = user.status != status
        user.status = status
        if status == UserStatus.BLOCKED:
            await SessionService(self.session).end_all(Subject(SubjectType.USER, user.id))
        await self.session.flush()
        if changed:  # a double click is not a second change
            await audit.record(
                self.session,
                actor=admin,
                action="user.blocked" if status == UserStatus.BLOCKED else "user.unblocked",
                target_type="user",
                target_id=user.id,
                target_label=user.user_code,
            )
        return await self._detail(user)

    async def delete(self, ref: str, admin: AdminUser) -> DeletionOut:
        user = await self._user(ref, for_update=True)
        if user.status == UserStatus.DELETED:
            raise UserDeletedException()
        deletion = await DeletionService(self.session).delete_now(
            user, DeletionSource.ADMIN, admin=admin
        )
        await audit.record(
            self.session,
            actor=admin,
            action="user.delete_requested",
            target_type="user",
            target_id=user.id,
            target_label=user.user_code,
        )
        return deletion_out(deletion)

    async def deletions(
        self, params: PaginationParams, status: DeletionStatus | None
    ) -> tuple[Sequence[DeletionOut], int]:
        conditions = [UserDeletion.status == status] if status else []
        rows = (
            await self.session.scalars(
                select(UserDeletion)
                .where(*conditions)
                .order_by(UserDeletion.created_at.desc(), UserDeletion.id)
                .offset(params.offset)
                .limit(params.size)
            )
        ).all()
        total = await self.session.scalar(
            select(func.count()).select_from(UserDeletion).where(*conditions)
        )
        return [deletion_out(r) for r in rows], total or 0

    async def _user(self, ref: str, *, for_update: bool = False) -> User:
        ref = ref.strip()
        if _CODE.fullmatch(ref.upper()):
            condition = User.user_code == ref.upper()
        else:
            try:
                condition = User.id == uuid.UUID(ref)
            except ValueError as exc:
                raise UserNotFoundException() from exc
        query = select(User).where(condition)
        user = await self.session.scalar(query.with_for_update() if for_update else query)
        if user is None:
            raise UserNotFoundException()
        return user

    async def _detail(self, user: User) -> AdminUserOut:
        area = (
            await self.session.scalar(select(Area.name).where(Area.id == user.area_id))
            if user.area_id
            else None
        )
        visits = await self.session.execute(
            select(Notch.notch_code, Venue.name, Notch.scanned_at, Notch.status)
            .join(Venue, Venue.id == Notch.venue_id)
            .where(Notch.user_id == user.id)
            .order_by(Notch.scanned_at.desc())
            .limit(RECENT_VISITS)
        )
        return AdminUserOut(
            **row_out(user).model_dump(),
            area=area,
            is_public=user.is_public,
            signup_source=user.signup_source,
            consent_at=user.consent_at,
            notches_total=await self.session.scalar(
                select(func.count()).where(Notch.user_id == user.id)
            )
            or 0,
            badges=list(
                await self.session.scalars(
                    select(UserBadge.badge).where(UserBadge.user_id == user.id)
                )
            ),
            visits=[
                AdminVisitOut(notch_code=c, venue_name=v, scanned_at=t, status=s)
                for c, v, t, s in visits
            ],
        )
