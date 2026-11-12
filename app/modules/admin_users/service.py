"""Admin Settings: the team's admin accounts, managed from the panel (role `admin` only).

    invite        name, email and role -> the account, and an emailed set-password link
    change        role (admin or reviewer) and on/off; turning someone off logs them out
    new link      a fresh set-password link (older links stop working)

Nobody can remove their own admin access, and there is always at least one active admin, so the
panel can't be locked. `python -m app.cli create-admin` stays for the very first admin.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailMessage
from app.core.logging import get_logger
from app.modules.admin_users.exceptions import (
    AdminEmailTakenException,
    AdminInactiveException,
    AdminLastAdminException,
    AdminSelfChangeException,
    AdminUserNotFoundException,
)
from app.modules.admin_users.models import AdminRole, AdminRoleDefinition, AdminUser
from app.modules.admin_users.roles import built_in_role
from app.modules.admin_users.schemas import AdminUserIn, AdminUserOut, AdminUserUpdate
from app.modules.auth.admin_auth import AdminAuthService
from app.modules.auth.enums import AccountStatus, RevokeReason, SubjectType
from app.modules.auth.sessions import SessionService, Subject

logger = get_logger(__name__)


def admin_out(admin: AdminUser) -> AdminUserOut:
    return AdminUserOut(
        id=admin.id,
        admin_user_code=admin.admin_user_code,
        name=admin.name,
        email=admin.email,
        role=admin.access,
        status=AccountStatus(admin.status),
        is_active=admin.status != AccountStatus.DISABLED,
        has_password=admin.password_hash is not None,
        last_login_at=admin.last_login_at,
        created_at=admin.created_at,
    )


class AdminUserService:
    def __init__(self, session: AsyncSession, auth: AdminAuthService) -> None:
        self.session = session
        self.auth = auth

    async def list(self) -> list[AdminUserOut]:
        admins = await self.session.scalars(select(AdminUser).order_by(AdminUser.created_at))
        return [admin_out(admin) for admin in admins]

    async def invite(
        self, actor: AdminUser, body: AdminUserIn
    ) -> tuple[AdminUserOut, EmailMessage]:
        if await self.session.scalar(select(AdminUser.id).where(AdminUser.email == body.email)):
            raise AdminEmailTakenException()
        admin = AdminUser(
            name=body.name.strip(),
            email=body.email,
            role=await built_in_role(self.session, body.role),
            invited_by_admin_id=actor.id,
        )
        self.session.add(admin)
        await self.session.flush()
        message = await self.auth.invite_email(admin)
        logger.info(
            "Admin %s invited %s as %s", actor.admin_user_code, admin.admin_user_code, body.role
        )
        return admin_out(admin), message

    async def update(self, actor: AdminUser, code: str, body: AdminUserUpdate) -> AdminUserOut:
        admin = await self._lock(code)
        role = body.role or admin.access
        was_on = admin.status != AccountStatus.DISABLED
        on = was_on if body.is_active is None else body.is_active
        loses_admin = (
            admin.access == AdminRole.ADMIN
            and admin.status == AccountStatus.ACTIVE
            and (role != AdminRole.ADMIN or not on)
        )
        if loses_admin and admin.id == actor.id:
            raise AdminSelfChangeException()
        if loses_admin and await self._other_active_admins(admin) == 0:
            raise AdminLastAdminException()

        if role != admin.access:
            admin.role = await built_in_role(self.session, role)
        turned_off = was_on and not on
        if turned_off:
            admin.status = AccountStatus.DISABLED
            admin.disabled_at = datetime.now(UTC)
            admin.disabled_by_admin_id = actor.id
        elif on and not was_on:
            # Back on: active again if they had set a password, else still to accept the invite.
            admin.status = (
                AccountStatus.ACTIVE if admin.password_hash is not None else AccountStatus.INVITED
            )
            admin.disabled_at = None
            admin.disabled_by_admin_id = None
        await self.session.flush()
        if turned_off:
            await SessionService(self.session).end_all(
                Subject(SubjectType.ADMIN, admin.id), RevokeReason.ACCOUNT_DISABLED
            )
        logger.info(
            "Admin %s set %s to %s, active=%s",
            actor.admin_user_code,
            admin.admin_user_code,
            role,
            on,
        )
        return admin_out(admin)

    async def new_link(self, actor: AdminUser, code: str) -> EmailMessage:
        admin = await self._lock(code)
        if admin.status == AccountStatus.DISABLED:
            raise AdminInactiveException()
        logger.info("Admin %s sent %s a new link", actor.admin_user_code, admin.admin_user_code)
        return await self.auth.invite_email(admin)

    async def _lock(self, code: str) -> AdminUser:
        admin = await self.session.scalar(
            select(AdminUser).where(AdminUser.admin_user_code == code).with_for_update(of=AdminUser)
        )
        if admin is None:
            raise AdminUserNotFoundException()
        return admin

    async def _other_active_admins(self, admin: AdminUser) -> int:
        # Locked, so two admins demoting each other at once can't both succeed.
        others = await self.session.scalars(
            select(AdminUser.id)
            .join(AdminRoleDefinition, AdminRoleDefinition.id == AdminUser.role_id)
            .where(
                AdminUser.id != admin.id,
                AdminRoleDefinition.is_owner.is_(True),
                AdminUser.status == AccountStatus.ACTIVE,
            )
            .with_for_update(of=AdminUser)
        )
        return len(others.all())
