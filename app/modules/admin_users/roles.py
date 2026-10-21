"""Roles and what each admin may do.

`permissions_of` is what every admin endpoint asks (via `RequirePermission`). The roles screen
uses `RoleService`. Two safety rules:

- The Owner role has every permission, present and future, and cannot be edited or deleted.
- An admin can only give a role permissions they have themselves, so nobody can build a role
  that is more powerful than their own.

Every change is in the audit log (`role.created`, `role.updated` with the permissions added and
removed, `role.deleted`).
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin_users.exceptions import (
    PermissionNotGrantableException,
    RoleInUseException,
    RoleLockedException,
    RoleNameTakenException,
    RoleNotFoundException,
)
from app.modules.admin_users.models import AdminRole, AdminRolePermission, AdminUser
from app.modules.admin_users.permissions import ALL_PERMISSIONS, Permission, known
from app.modules.admin_users.schemas import RoleCreate, RoleUpdate
from app.modules.audit import service as audit

_CODE = re.compile(r"ROL-[0-9A-Z]{8}")


async def role_permissions(session: AsyncSession, role: AdminRole) -> frozenset[Permission]:
    if role.is_owner:
        return ALL_PERMISSIONS
    keys = await session.scalars(
        select(AdminRolePermission.permission).where(AdminRolePermission.role_id == role.id)
    )
    return known(keys)


async def permissions_of(
    session: AsyncSession, admin: AdminUser
) -> tuple[AdminRole, frozenset[Permission]]:
    """The admin's role and everything it allows."""
    role = await session.get(AdminRole, admin.role_id)
    if role is None:  # the foreign key makes this impossible
        return AdminRole(name="?", is_owner=False), frozenset()
    return role, await role_permissions(session, role)


@dataclass(frozen=True, slots=True)
class RoleView:
    role: AdminRole
    permissions: frozenset[Permission]
    admin_count: int


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self) -> list[RoleView]:
        """Owner first, then by name."""
        roles = (
            await self.session.scalars(
                select(AdminRole).order_by(AdminRole.is_owner.desc(), func.lower(AdminRole.name))
            )
        ).all()
        counts = dict(
            (
                await self.session.execute(
                    select(AdminUser.role_id, func.count()).group_by(AdminUser.role_id)
                )
            ).all()
        )
        return [
            RoleView(role, await role_permissions(self.session, role), counts.get(role.id, 0))
            for role in roles
        ]

    async def get(self, ref: str, *, for_update: bool = False) -> AdminRole:
        """By id or by public code (`ROL-7K3M9QX2`, any case)."""
        ref = ref.strip()
        if _CODE.fullmatch(ref.upper()):
            condition = AdminRole.admin_role_code == ref.upper()
        else:
            try:
                condition = AdminRole.id == uuid.UUID(ref)
            except ValueError as exc:
                raise RoleNotFoundException() from exc
        query = select(AdminRole).where(condition)
        role = await self.session.scalar(query.with_for_update() if for_update else query)
        if role is None:
            raise RoleNotFoundException()
        return role

    async def view(self, role: AdminRole) -> RoleView:
        count = await self.session.scalar(
            select(func.count()).select_from(AdminUser).where(AdminUser.role_id == role.id)
        )
        return RoleView(role, await role_permissions(self.session, role), count or 0)

    async def create(self, actor: AdminUser, body: RoleCreate) -> RoleView:
        await self._check_grantable(actor, body.permissions)
        role = AdminRole(name=body.name, description=body.description or None)
        self.session.add(role)
        await self._save_name(role)
        self._set_permissions(role, body.permissions)
        await self.session.flush()
        await audit.record(
            self.session,
            audit.Actor.admin(actor),
            "role.created",
            role,
            after={
                "name": role.name,
                "description": role.description,
                "permissions": body.permissions,
            },
        )
        return await self.view(role)

    async def update(self, actor: AdminUser, ref: str, body: RoleUpdate) -> RoleView:
        role = await self.get(ref, for_update=True)
        if role.is_owner:
            raise RoleLockedException()
        before: dict[str, object] = {}
        after: dict[str, object] = {}
        fields = body.model_fields_set

        if "name" in fields and body.name != role.name:
            before["name"], after["name"] = role.name, body.name
            role.name = body.name  # type: ignore[assignment]
            await self._save_name(role)
        if "description" in fields and (body.description or None) != role.description:
            before["description"], after["description"] = role.description, body.description
            role.description = body.description or None
        if "permissions" in fields and body.permissions is not None:
            current = await role_permissions(self.session, role)
            wanted = frozenset(body.permissions)
            # Only what actually changes must be grantable: an admin can still edit the name of a
            # role that has permissions they don't have.
            await self._check_grantable(actor, list(wanted ^ current))
            if wanted != current:
                before["permissions_removed"] = sorted(current - wanted)
                after["permissions_added"] = sorted(wanted - current)
                await self.session.execute(
                    delete(AdminRolePermission).where(AdminRolePermission.role_id == role.id)
                )
                self._set_permissions(role, list(wanted))

        if after or before:
            await self.session.flush()
            await self.session.refresh(role)  # updated_at is set by the database
            await audit.record(
                self.session,
                audit.Actor.admin(actor),
                "role.updated",
                role,
                before=before,
                after=after,
            )
        return await self.view(role)

    async def delete(self, actor: AdminUser, ref: str) -> None:
        role = await self.get(ref, for_update=True)
        if role.is_owner:
            raise RoleLockedException()
        in_use = await self.session.scalar(
            select(func.count()).select_from(AdminUser).where(AdminUser.role_id == role.id)
        )
        if in_use:
            raise RoleInUseException()
        permissions = await role_permissions(self.session, role)
        await audit.record(
            self.session,
            audit.Actor.admin(actor),
            "role.deleted",
            role,
            before={"name": role.name, "permissions": sorted(permissions)},
        )
        await self.session.delete(role)
        await self.session.flush()

    async def _check_grantable(self, actor: AdminUser, permissions: Sequence[Permission]) -> None:
        _, mine = await permissions_of(self.session, actor)
        if not set(permissions) <= mine:
            raise PermissionNotGrantableException(
                details={"not_yours": sorted(set(permissions) - mine)}
            )

    async def _save_name(self, role: AdminRole) -> None:
        try:
            async with self.session.begin_nested():  # unique name, any case: the final guard
                await self.session.flush()
        except IntegrityError as exc:
            raise RoleNameTakenException() from exc

    def _set_permissions(self, role: AdminRole, permissions: Sequence[Permission]) -> None:
        self.session.add_all(
            AdminRolePermission(role_id=role.id, permission=p) for p in sorted(set(permissions))
        )
