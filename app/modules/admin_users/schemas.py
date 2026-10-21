"""Request and response bodies for roles and permissions (the admin panel's roles screen)."""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from app.common.schemas import BaseSchema, EntitySchema, PublicCode
from app.modules.admin_users.permissions import Permission

MAX_ROLE_NAME_LENGTH = 40
MAX_ROLE_DESCRIPTION_LENGTH = 200


def _unique_sorted(value: list[Permission]) -> list[Permission]:
    return sorted(set(value))


Permissions = Annotated[list[Permission], AfterValidator(_unique_sorted)]


class PermissionOut(BaseSchema):
    key: Permission
    group: str
    label: str
    description: str


class RoleBriefOut(BaseSchema):
    """The role shown next to an admin."""

    id: uuid.UUID
    admin_role_code: PublicCode
    name: str
    is_owner: bool


class RoleOut(EntitySchema):
    admin_role_code: PublicCode
    name: str
    description: str | None
    is_owner: bool
    # Every permission for the Owner role.
    permissions: list[Permission]
    admin_count: int
    created_at: datetime
    updated_at: datetime


class RoleCreate(BaseSchema):
    name: str = Field(min_length=1, max_length=MAX_ROLE_NAME_LENGTH, examples=["Support"])
    description: str | None = Field(None, max_length=MAX_ROLE_DESCRIPTION_LENGTH)
    permissions: Permissions = Field(
        default_factory=list, examples=[["customers.view", "whatsapp.view"]]
    )


class RoleUpdate(BaseSchema):
    """Only the fields sent are changed. `permissions` replaces the whole list."""

    name: str | None = Field(None, min_length=1, max_length=MAX_ROLE_NAME_LENGTH)
    description: str | None = Field(None, max_length=MAX_ROLE_DESCRIPTION_LENGTH)
    permissions: Permissions | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Send at least one of name, description, permissions")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("A role needs a name")
        if "permissions" in self.model_fields_set and self.permissions is None:
            raise ValueError("Send a list of permissions (it may be empty)")
        return self
