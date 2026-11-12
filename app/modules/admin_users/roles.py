"""The two built-in admin roles made by migration 0012, looked up by access level.

Custom roles (ticked permissions) come with the admin module's roles screen; until then every
admin has one of these two, and the API's `admin` / `reviewer` maps onto them.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin_users.models import REVIEWER_ROLE_NAME, AdminRole, AdminRoleDefinition


async def built_in_role(session: AsyncSession, access: AdminRole) -> AdminRoleDefinition:
    if access == AdminRole.ADMIN:
        query = select(AdminRoleDefinition).where(AdminRoleDefinition.is_owner.is_(True))
    else:
        query = select(AdminRoleDefinition).where(
            AdminRoleDefinition.is_owner.is_(False),
            func.lower(AdminRoleDefinition.name) == REVIEWER_ROLE_NAME.lower(),
        )
    role = await session.scalar(query)
    if role is None:  # only if migration 0012 did not run
        raise RuntimeError(f"Built-in admin role for {access} is missing")
    return role
