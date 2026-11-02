"""Deleting a customer's data: "Delete everything" in the passport (C12), DELETE then YES on
WhatsApp (B9), or an admin handling a request (G3).

    close   the account stops at once: status `deleted`, sessions ended, no more bot replies
    wipe    a job (every minute) then removes the personal data for good: the user row, their
            badges, cards and login tokens, and every WhatsApp message to or from the number.
            Their Notchs stay as anonymous visits (user_id NULL), so venues' totals and bills
            do not change.

A `user_deletions` row records each request without personal data (G3). It is also the
pending state of DELETE: YES confirms the newest request from the last 10 minutes, under a row
lock and in the same transaction as the closing, so a retried or doubled YES closes the
account once and a failed one leaves the request to confirm again.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin_users.models import AdminUser
from app.modules.audit import service as audit
from app.modules.auth.enums import SubjectType
from app.modules.auth.sessions import SessionService, Subject
from app.modules.notches.models import PendingCheckIn
from app.modules.users.models import (
    DeletionSource,
    DeletionStatus,
    User,
    UserDeletion,
    UserStatus,
)
from app.modules.whatsapp.models import WhatsAppMessage

CONFIRM_WINDOW = timedelta(minutes=10)
WIPE_BATCH = 50


class DeletionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ask(self, user: User) -> UserDeletion:
        """DELETE on WhatsApp: nothing happens until the customer replies YES."""
        deletion = self._new(user, DeletionSource.WHATSAPP, DeletionStatus.AWAITING_YES)
        await self.session.flush()
        return deletion

    def _waiting(self, user: User) -> Select[tuple[UserDeletion]]:
        return select(UserDeletion).where(
            UserDeletion.user_id == user.id,
            UserDeletion.status == DeletionStatus.AWAITING_YES,
            UserDeletion.created_at > datetime.now(UTC) - CONFIRM_WINDOW,
        )

    async def is_waiting_for_yes(self, user: User) -> bool:
        return await self.session.scalar(self._waiting(user).limit(1)) is not None

    async def confirm(self, user: User) -> bool:
        """YES after DELETE. False if nothing was waiting (or it timed out)."""
        deletion = await self.session.scalar(
            self._waiting(user).order_by(UserDeletion.created_at.desc()).limit(1).with_for_update()
        )
        if deletion is None:
            return False
        await self._close(user, deletion)
        return True

    async def delete_now(
        self, user: User, source: DeletionSource, *, admin: AdminUser | None = None
    ) -> UserDeletion:
        """The passport's "Yes, delete everything", or an admin: no second question."""
        deletion = self._new(user, source, DeletionStatus.AWAITING_YES, admin=admin)
        await self.session.flush()
        await self._close(user, deletion)
        return deletion

    def _new(
        self,
        user: User,
        source: DeletionSource,
        status: DeletionStatus,
        *,
        admin: AdminUser | None = None,
    ) -> UserDeletion:
        deletion = UserDeletion(
            user_id=user.id,
            user_code=user.user_code,
            phone_last4=user.phone_e164[-4:],
            source=source,
            status=status,
            requested_by_admin_id=admin.id if admin else None,
        )
        self.session.add(deletion)
        return deletion

    async def _close(self, user: User, deletion: UserDeletion) -> None:
        now = datetime.now(UTC)
        user.status = UserStatus.DELETED
        user.deleted_at = now
        deletion.status = DeletionStatus.CONFIRMED
        deletion.confirmed_at = now
        # Any other request still waiting for YES is settled by this one.
        await self.session.execute(
            update(UserDeletion)
            .where(
                UserDeletion.user_id == user.id,
                UserDeletion.status == DeletionStatus.AWAITING_YES,
                UserDeletion.id != deletion.id,
            )
            .values(status=DeletionStatus.EXPIRED)
        )
        await self.session.execute(delete(PendingCheckIn).where(PendingCheckIn.user_id == user.id))
        await SessionService(self.session).end_all(Subject(SubjectType.USER, user.id))
        await self.session.flush()


async def wipe_deleted(session: AsyncSession, *, batch: int = WIPE_BATCH) -> int:
    """Remove the personal data of closed accounts. Returns how many customers were wiped.
    Also marks DELETE requests that were never confirmed as expired."""
    now = datetime.now(UTC)
    users = (
        await session.scalars(
            select(User)
            .where(User.status == UserStatus.DELETED, User.deleted_at.is_not(None))
            .order_by(User.deleted_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for user in users:
        await session.execute(
            delete(WhatsAppMessage).where(WhatsAppMessage.phone_e164 == user.phone_e164)
        )
        # Badges, cards and login tokens go with the row (ON DELETE CASCADE); Notchs keep
        # counting for the venue as anonymous visits (ON DELETE SET NULL).
        await session.execute(delete(User).where(User.id == user.id))
        await session.execute(
            update(UserDeletion)
            .where(
                UserDeletion.user_id == user.id,
                UserDeletion.status == DeletionStatus.CONFIRMED,
            )
            .values(status=DeletionStatus.DONE, completed_at=now)
        )
        await audit.record(
            session,
            actor=None,
            action="user.deleted",
            target_type="user",
            target_id=user.id,
            target_label=user.user_code,
            details={"reason": "deletion request"},
        )
    await session.execute(
        update(UserDeletion)
        .where(
            UserDeletion.status == DeletionStatus.AWAITING_YES,
            UserDeletion.created_at < now - CONFIRM_WINDOW,
        )
        .values(status=DeletionStatus.EXPIRED)
    )
    return len(users)
