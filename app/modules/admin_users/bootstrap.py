"""The very first admin, without a shell (Render's free plan has none for `create-admin`).

Set FIRST_ADMIN_EMAIL (and FIRST_ADMIN_NAME) on the API and redeploy: at startup, and only while
no admin exists at all, that address becomes an admin (role `admin`) and is emailed a link to set
a password. Once any admin exists this does nothing, so it can't add admins later; further admins
are added in admin Settings. If the email doesn't arrive, "Forgot password" on the admin login
page sends a new link.
"""

from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailClient
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.admin_users.roles import built_in_role
from app.modules.auth.admin_auth import AdminAuthService

logger = get_logger(__name__)


async def ensure_first_admin(
    session: AsyncSession, redis: Redis, email_client: EmailClient
) -> bool:
    """Create the first admin from FIRST_ADMIN_EMAIL if there is none yet. True if created."""
    email = settings.FIRST_ADMIN_EMAIL
    if not email:
        return False
    # Two API instances starting at once must not both create one.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('first-admin'))"))
    if await session.scalar(select(AdminUser.id).limit(1)) is not None:
        logger.info("FIRST_ADMIN_EMAIL is set but admins already exist: ignored")
        await session.rollback()
        return False
    admin = AdminUser(
        email=email,
        name=settings.FIRST_ADMIN_NAME.strip()[:60],
        role=await built_in_role(session, AdminRole.ADMIN),
    )
    session.add(admin)
    await session.flush()
    message = await AdminAuthService(session, redis).invite_email(admin)
    await session.commit()
    await email_client.send(message)
    logger.info("Created the first admin %s; set-password link emailed", admin.admin_user_code)
    return True
