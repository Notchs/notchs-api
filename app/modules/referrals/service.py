"""Invites and Star Points.

    a customer shares their link: notchs.com/?ref=<their handle>
    a newcomer adds who invited them (their handle), within their first 14 days
    the inviter gets 50 Star Points when the newcomer gets their first real Notch

Rewarding on a real Notch, not on sign-up, means a made-up number earns nothing: someone has to
scan at a counter. An inviter is rewarded for at most 10 friends a month. Invites can't loop
(A invites B, B invites A) and nobody invites themselves.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ids import new_uuid
from app.common.time import ist_date
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.referrals.exceptions import (
    ReferralCodeInvalidException,
    ReferralNotAllowedException,
)
from app.modules.referrals.models import (
    Referral,
    ReferralStatus,
    StarPointsEntry,
    StarPointsReason,
)
from app.modules.referrals.schemas import ReferralOut
from app.modules.scans.models import Visit, VisitStatus
from app.modules.users.models import User, UserStatus

logger = get_logger(__name__)

REFERRAL_STAR_POINTS = 50
ADD_INVITER_WITHIN = timedelta(days=14)
MAX_REWARDS_PER_MONTH = 10


def invite_link(handle: str) -> str:
    return f"{settings.CUSTOMER_APP_URL}/?ref={handle}"


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def card(self, user: User, now: datetime | None = None) -> ReferralOut:
        now = now or datetime.now(UTC)
        star_points = await self.session.scalar(
            select(func.coalesce(func.sum(StarPointsEntry.points), 0)).where(
                StarPointsEntry.user_id == user.id
            )
        )
        invited, rewarded = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(Referral.status == ReferralStatus.REWARDED),
                ).where(Referral.referrer_id == user.id)
            )
        ).one()
        inviter = await self.session.scalar(
            select(User.handle)
            .join(Referral, Referral.referrer_id == User.id)
            .where(Referral.referred_id == user.id)
        )
        return ReferralOut(
            code=user.handle,
            link=invite_link(user.handle),
            star_points=star_points or 0,
            invited=invited,
            rewarded=rewarded,
            invited_by=inviter,
            can_add_inviter=inviter is None and now - user.created_at <= ADD_INVITER_WITHIN,
        )

    async def add_inviter(self, user: User, code: str, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        if now - user.created_at > ADD_INVITER_WITHIN:
            raise ReferralNotAllowedException()
        inviter = await self.session.scalar(
            select(User).where(
                User.handle == code.strip().removeprefix("@").lower(),
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
        )
        if inviter is None or inviter.id == user.id:
            raise ReferralCodeInvalidException()
        # No loops: someone this customer invited can't become their inviter.
        loop = await self.session.scalar(
            select(Referral.id).where(
                Referral.referrer_id == user.id, Referral.referred_id == inviter.id
            )
        )
        if loop is not None:
            raise ReferralCodeInvalidException()
        added = await self.session.scalar(
            insert(Referral)
            .values(id=new_uuid(), referrer_id=inviter.id, referred_id=user.id)
            .on_conflict_do_nothing(index_elements=[Referral.referred_id])
            .returning(Referral.id)
        )
        if added is None:  # already has an inviter (a double submit lands here too)
            raise ReferralNotAllowedException()
        logger.info("Customer %s added inviter %s", user.id, inviter.id)
        # Already notched before adding the inviter: reward now.
        await reward_first_notch(self.session, user.id, now=now, require_visit=True)


async def reward_first_notch(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    now: datetime,
    require_visit: bool = False,
    visit_id: uuid.UUID | None = None,
) -> bool:
    """Called when a customer gets a counted Notch (`visit_id`), or with `require_visit` when
    an inviter is added after their first Notch. Rewards their inviter once. True if Star
    Points were given. The referral row is locked, so two Notchs at once reward once."""
    referral = await session.scalar(
        select(Referral)
        .where(Referral.referred_id == user_id, Referral.status == ReferralStatus.PENDING)
        .with_for_update(skip_locked=True)
    )
    if referral is None:
        return False
    if require_visit:
        visit_id = await session.scalar(
            select(Visit.id)
            .where(Visit.user_id == user_id, Visit.status == VisitStatus.VALID)
            .order_by(Visit.visited_at)
            .limit(1)
        )
        if visit_id is None:
            return False
    referral.rewarded_at = now
    referral.qualifying_visit_id = visit_id
    month_start = ist_date(now).replace(day=1)
    rewarded_this_month = await session.scalar(
        select(func.count())
        .select_from(StarPointsEntry)
        .where(
            StarPointsEntry.user_id == referral.referrer_id,
            StarPointsEntry.reason == StarPointsReason.REFERRAL,
            func.timezone("Asia/Kolkata", StarPointsEntry.created_at) >= month_start,
        )
    )
    if (rewarded_this_month or 0) >= MAX_REWARDS_PER_MONTH:
        referral.status = ReferralStatus.CAPPED
        await session.flush()
        logger.info("Referral %s over the monthly cap: no Star Points", referral.id)
        return False
    referral.status = ReferralStatus.REWARDED
    session.add(
        StarPointsEntry(
            user_id=referral.referrer_id,
            referral_id=referral.id,
            reason=StarPointsReason.REFERRAL,
            points=REFERRAL_STAR_POINTS,
        )
    )
    await session.flush()
    return True


async def cancel_for_removed_visit(session: AsyncSession, visit_id: uuid.UUID) -> int:
    """A Notch was removed as fraud: invites it triggered are cancelled and their Star Points
    taken back (team decision, 12 Nov 2026), so a fake Notch never pays out. A balance never
    goes below zero: only what is left is taken back. Returns how many invites were cancelled."""
    referrals = (
        await session.scalars(
            select(Referral)
            .where(
                Referral.qualifying_visit_id == visit_id,
                Referral.status.in_((ReferralStatus.REWARDED, ReferralStatus.CAPPED)),
            )
            .with_for_update()
        )
    ).all()
    for referral in referrals:
        if referral.status == ReferralStatus.REWARDED:
            # Lock the inviter's rows so two removals can't both read the same balance.
            await session.execute(
                select(StarPointsEntry.id)
                .where(StarPointsEntry.user_id == referral.referrer_id)
                .with_for_update()
            )
            balance = await session.scalar(
                select(func.coalesce(func.sum(StarPointsEntry.points), 0)).where(
                    StarPointsEntry.user_id == referral.referrer_id
                )
            )
            take_back = min(REFERRAL_STAR_POINTS, balance or 0)
            if take_back > 0:
                session.add(
                    StarPointsEntry(
                        user_id=referral.referrer_id,
                        referral_id=referral.id,
                        reason=StarPointsReason.REFERRAL_REVERSED,
                        points=-take_back,
                    )
                )
        referral.status = ReferralStatus.CANCELLED
        logger.info("Referral %s cancelled: its qualifying Notch was removed", referral.id)
    await session.flush()
    return len(referrals)
