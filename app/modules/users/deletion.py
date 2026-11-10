"""Deleting a customer's account, for good (team decision: deletion is permanent; DPDP).

Everything that says who they are goes: phone number, name, Instagram, area, consent, logins,
bill-photo claims, invites and Star Points, and the number, text and raw data of every WhatsApp
message to or from them.

Their visits stay, anonymous (team decision), so venues' past totals don't change: the account
row is kept as an empty shell with a random handle, status `deleted` and no phone number. It
never shows on leaderboards, admin lists or venue screens, and it can't log in. A later message
from the same number starts a brand-new account.
"""

import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.auth.models import AuthToken
from app.modules.claims.models import VisitClaim
from app.modules.leaderboards.service import KEY_PATTERN
from app.modules.referrals.models import Referral, StarPointsEntry
from app.modules.users.handles import free_handle
from app.modules.users.models import User, UserStatus, UserTheme
from app.modules.users.repository import UserRepository
from app.modules.whatsapp.models import WhatsAppMessage

logger = get_logger(__name__)

CONFIRM_WORD = "DELETE"


async def delete_customer(
    session: AsyncSession, redis: Redis, user_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Wipe the customer and commit. False if they were already deleted."""
    now = now or datetime.now(UTC)
    user = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.deleted_at is not None:
        return False
    phone = user.phone_e164

    await session.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
    await session.execute(delete(VisitClaim).where(VisitClaim.user_id == user.id))
    await session.execute(delete(StarPointsEntry).where(StarPointsEntry.user_id == user.id))
    await session.execute(
        delete(Referral).where(
            or_(Referral.referrer_id == user.id, Referral.referred_id == user.id)
        )
    )
    # The rows stay (scans point at them), but nothing in them identifies the person.
    theirs = [WhatsAppMessage.user_id == user.id]
    if phone:
        theirs.append(WhatsAppMessage.phone_e164 == phone)
    await session.execute(
        update(WhatsAppMessage).where(or_(*theirs)).values(phone_e164=None, body=None, payload=None)
    )

    user.phone_e164 = None
    user.name = None
    user.handle = await free_handle(None, taken=UserRepository(session).handle_exists)
    user.instagram_handle = None
    user.show_instagram_to_venues = False
    user.is_public = False
    user.area_id = None
    user.age_confirmed_at = None
    user.consent_at = None
    user.consent_version = None
    user.last_seen_at = None
    user.theme = UserTheme.LIGHT
    user.status = UserStatus.DELETED
    user.deleted_at = now
    await session.commit()
    logger.info("Customer %s deleted their account", user.id)

    # Off the leaderboards now; the next rebuild would drop them anyway.
    try:
        keys = [key async for key in redis.scan_iter(match=KEY_PATTERN)]
        async with redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.zrem(key, str(user.id))
            pipe.delete(f"pc:user:{user.id}")
            await pipe.execute()
    except RedisError:
        logger.exception("Customer %s: leaderboard clean-up waits for the next rebuild", user.id)
    return True
