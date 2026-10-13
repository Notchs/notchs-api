"""Notchs staff accounts. Login (password, sessions) is built in the auth module."""

from sqlalchemy import Boolean, CheckConstraint, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, public_code_column


class AdminUser(Entity):
    __tablename__ = "admin_users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    admin_user_code: Mapped[str] = public_code_column("ADM")
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    # NULL until the admin sets a password.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
