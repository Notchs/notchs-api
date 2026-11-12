"""An admin made from settings, without a shell (Render's free plan has none for `create-admin`)
and without needing email to work yet.

Set on the API, then redeploy:

    FIRST_ADMIN_EMAIL      the admin's email
    FIRST_ADMIN_NAME       their name (optional)
    FIRST_ADMIN_PASSWORD   a password of 10+ characters (optional but recommended)

At startup:
- No admin has that email: one is created (role `admin`). With a password it can log in at once;
  without one it is emailed a set-password link.
- That admin exists but never set a password (e.g. the link never arrived): the password is set.
- That admin already has a password, or was turned off: nothing changes. It is never reset here.

After logging in, change the password in the panel and remove FIRST_ADMIN_PASSWORD from the
settings. Whoever can change these settings can already reach the database, so this gives
nobody new power. More admins are added in admin Settings.
"""

from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailClient, EmailSendError
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.auth.admin_auth import AdminAuthService
from app.modules.auth.exceptions import PasswordInvalidException
from app.modules.auth.passwords import check_password_policy, hash_password_async

logger = get_logger(__name__)


async def _password_hash(email: str) -> str | None:
    """The hash of FIRST_ADMIN_PASSWORD, or None if it is unset or breaks the password rules."""
    secret = settings.FIRST_ADMIN_PASSWORD
    if secret is None or not secret.get_secret_value():
        return None
    password = secret.get_secret_value()
    try:
        check_password_policy(password, email=email)
    except PasswordInvalidException as exc:
        # Never the value itself.
        logger.error("FIRST_ADMIN_PASSWORD was not used: %s", exc.message)
        return None
    return await hash_password_async(password)


async def ensure_first_admin(
    session: AsyncSession, redis: Redis, email_client: EmailClient
) -> bool:
    """Create the admin from FIRST_ADMIN_EMAIL, or give it its first password. True if anything
    changed."""
    email = settings.FIRST_ADMIN_EMAIL
    if not email:
        return False
    # Two API instances starting at once must not both create it.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('first-admin'))"))
    admin = await session.scalar(select(AdminUser).where(AdminUser.email == email))

    if admin is not None:
        if admin.password_hash is not None or not admin.is_active:
            await session.rollback()
            return False
        password_hash = await _password_hash(email)
        if password_hash is None:
            await session.rollback()
            return False
        admin.password_hash = password_hash
        await session.commit()
        logger.info("Admin %s got its first password from settings", admin.admin_user_code)
        return True

    password_hash = await _password_hash(email)
    admin = AdminUser(
        email=email,
        name=settings.FIRST_ADMIN_NAME.strip()[:60] or "Notchs admin",
        role=AdminRole.ADMIN,
        password_hash=password_hash,
    )
    session.add(admin)
    await session.flush()
    if password_hash is not None:
        await session.commit()
        logger.info("Created admin %s from settings, with a password", admin.admin_user_code)
        return True
    message = await AdminAuthService(session, redis).invite_email(admin)
    await session.commit()
    try:
        await email_client.send(message)
    except EmailSendError:
        logger.exception(
            "Created admin %s but the set-password email failed; set FIRST_ADMIN_PASSWORD",
            admin.admin_user_code,
        )
        return True
    logger.info("Created admin %s from settings; set-password link emailed", admin.admin_user_code)
    return True
