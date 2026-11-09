"""The team's admin accounts, as admins see them in Settings."""

import uuid
from datetime import datetime

from pydantic import Field

from app.common.schemas import BaseSchema, PublicCode
from app.modules.admin_users.models import AdminRole
from app.modules.auth.emails import Email


class AdminUserOut(BaseSchema):
    id: uuid.UUID
    admin_user_code: PublicCode
    name: str
    email: str
    role: AdminRole
    is_active: bool
    # Whether they have set a password yet (a new admin hasn't until they open their link).
    has_password: bool
    last_login_at: datetime | None
    created_at: datetime


class AdminUserIn(BaseSchema):
    name: str = Field(min_length=2, max_length=60)
    email: Email
    role: AdminRole = AdminRole.REVIEWER


class AdminUserUpdate(BaseSchema):
    role: AdminRole | None = None
    is_active: bool | None = None
