"""Notchs team accounts for the admin panel, and the roles that decide what each can do.

Login (password, sessions) is in the auth module. Roles are data: the Owner builds them in the
panel by ticking permissions from the fixed list in `permissions.py`. Every admin has one role.
The built-in Owner role (`is_owner`) has every permission, present and future, and cannot be
edited or deleted.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Entity, public_code_column

OWNER_ROLE_NAME = "Owner"
REVIEWER_ROLE_NAME = "Reviewer"


class AdminRole(Entity):
    __tablename__ = "admin_roles"
    __table_args__ = (
        # Names are unique whatever their case ("Support" and "support" are the same role).
        Index("uq_admin_roles_name_lower", text("lower(name)"), unique=True),
        # Exactly one built-in Owner role.
        Index("uq_admin_roles_owner", "is_owner", unique=True, postgresql_where=text("is_owner")),
    )

    admin_role_code: Mapped[str] = public_code_column("ROL")
    name: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(String(200))
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class AdminRolePermission(Base):
    """One ticked permission of a role. Unknown keys (a permission later removed from the code)
    are ignored when read."""

    __tablename__ = "admin_role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(40), primary_key=True)


class AdminUser(Entity):
    __tablename__ = "admin_users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    # Public id shown to people (README, "IDs and public codes"). Not in the sheet.
    admin_user_code: Mapped[str] = public_code_column("ADM")
    # An @notchs.com address.
    email: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    # A role in use cannot be deleted.
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_roles.id", ondelete="RESTRICT"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # argon2id. NULL until the admin sets a password. Not in the sheet (login design is separate).
    password_hash: Mapped[str | None] = mapped_column(String(255))
