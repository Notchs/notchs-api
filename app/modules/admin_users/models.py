"""Notchs team accounts for the admin panel. Login (password, sessions) is in the auth module."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values, public_code_column


class AdminRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"


class AdminUser(Entity):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(in_values("role", AdminRole), name="role_valid"),
    )

    # Public id shown to people (README, "IDs and public codes"). Not in the sheet.
    admin_user_code: Mapped[str] = public_code_column("ADM")
    # An @notchs.com address.
    email: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(
        String(12), default=AdminRole.REVIEWER, server_default=AdminRole.REVIEWER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # argon2id. NULL until the admin sets a password. Not in the sheet (login design is separate).
    password_hash: Mapped[str | None] = mapped_column(String(255))
