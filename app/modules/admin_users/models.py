"""Notchs team accounts for the admin panel, and the roles that decide what each can do.

Login (password, sessions) is in the auth module. Roles are data (`admin_roles`, with ticked
permissions in `admin_role_permissions`). Until the roles screen exists (admin module) there are
two built-in roles, made by migration 0012: "Admin" (`is_owner`: everything) and "Reviewer".
The API still speaks of the access level `admin` / `reviewer` (`AdminRole`), worked out from them.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Entity, in_values, public_code_column
from app.modules.auth.enums import AccountStatus

ADMIN_ROLE_NAME = "Admin"
REVIEWER_ROLE_NAME = "Reviewer"


class AdminRole(StrEnum):
    """Access level shown by the API: `admin` = a role with `is_owner`, else `reviewer`."""

    ADMIN = "admin"
    REVIEWER = "reviewer"


class AdminRoleDefinition(Entity):
    """A role in `admin_roles`. Named so it does not clash with the `AdminRole` access level."""

    __tablename__ = "admin_roles"
    __table_args__ = (
        # Names are unique whatever their case ("Support" and "support" are the same role).
        Index("uq_admin_roles_name_lower", text("lower(name)"), unique=True),
        # Exactly one built-in owner role.
        Index("uq_admin_roles_owner", "is_owner", unique=True, postgresql_where=text("is_owner")),
    )

    admin_role_code: Mapped[str] = public_code_column("ROL")
    name: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(String(200))
    # Every permission, present and future. Cannot be edited or deleted.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class AdminRolePermission(Base):
    """One ticked permission of a role. Unknown keys are ignored when read."""

    __tablename__ = "admin_role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(40), primary_key=True)


class AdminUser(Entity):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(in_values("status", AccountStatus), name="status_valid"),
        # Rows disabled before 0012 have no date; a date is only ever set on a disabled row.
        CheckConstraint(
            "status = 'disabled' OR disabled_at IS NULL", name="disabled_at_only_when_disabled"
        ),
        CheckConstraint(
            "status <> 'active' OR password_hash IS NOT NULL", name="active_has_password"
        ),
        CheckConstraint(
            "(mfa_enabled_at IS NULL) OR (mfa_secret_enc IS NOT NULL)", name="mfa_has_secret"
        ),
    )

    # Public id shown to people (README, "IDs and public codes"). Not in the sheet.
    admin_user_code: Mapped[str] = public_code_column("ADM")
    # An @notchs.com address.
    email: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    # A role in use cannot be deleted.
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_roles.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(10), default=AccountStatus.INVITED, server_default=AccountStatus.INVITED.value
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # argon2id. NULL until the admin sets a password. Not in the sheet (login design is separate).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Two-factor login (TOTP). Fernet-encrypted like counter secrets, never stored plain. The
    # login step and setup screen come later; until then both stay empty.
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(255))
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    # Team decision 12 Nov 2026: stored, E.164 via app/common/phone.py. Never in logs or lists
    # outside the team screen.
    phone_e164: Mapped[str | None] = mapped_column(String(16))

    # Always loaded with the admin (one join), so `access` never needs another query. Set the
    # object (`admin.role = role`), not just `role_id`, when creating or changing an admin.
    role: Mapped[AdminRoleDefinition] = relationship(lazy="joined", innerjoin=True)

    @property
    def access(self) -> AdminRole:
        return AdminRole.ADMIN if self.role.is_owner else AdminRole.REVIEWER
