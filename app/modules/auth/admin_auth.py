"""Admin login (email + password), on the shared mechanics in password_auth.py."""

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailMessage
from app.core.config import settings
from app.modules.admin_users.models import AdminUser
from app.modules.auth import messages
from app.modules.auth.enums import SubjectType
from app.modules.auth.password_auth import AccountKind, PasswordAuthService, PasswordLogin
from app.modules.auth.sessions import Subject, active_admin


def admin_subject(admin: AdminUser) -> Subject:
    return Subject(SubjectType.ADMIN, admin.id)


ADMINS = AccountKind(
    key="admin",
    subject_type=SubjectType.ADMIN,
    model=AdminUser,
    load_active=active_admin,
    subject_of=admin_subject,
    subject_id_of=lambda token: token.admin_user_id,
    reset_email=lambda admin, raw: messages.password_reset_email(
        to=admin.email, name=admin.name, raw_token=raw, app_url=settings.ADMIN_APP_URL
    ),
    changed_email=lambda admin: messages.password_changed_email(
        to=admin.email, name=admin.name, app_url=settings.ADMIN_APP_URL
    ),
)


class AdminAuthService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.passwords = PasswordAuthService(session, redis, ADMINS)

    async def login(
        self, email: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> PasswordLogin[AdminUser]:
        return await self.passwords.login(email, password, ip=ip, user_agent=user_agent)

    async def set_password(
        self, raw_token: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> PasswordLogin[AdminUser]:
        return await self.passwords.set_password(raw_token, password, ip=ip, user_agent=user_agent)

    async def forgot_password(self, email: str, *, ip: str | None) -> EmailMessage | None:
        return await self.passwords.forgot_password(email, ip=ip)

    async def change_password(
        self,
        admin: AdminUser,
        current_password: str,
        new_password: str,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[PasswordLogin[AdminUser], EmailMessage]:
        return await self.passwords.change_password(
            admin, current_password, new_password, ip=ip, user_agent=user_agent
        )

    async def invite_email(self, admin: AdminUser) -> EmailMessage:
        """Set-password link for a new admin (created with `python -m app.cli create-admin`)."""
        raw = await self.passwords.issue_set_password_token(admin)
        return messages.admin_invite_email(to=admin.email, name=admin.name, raw_token=raw)
