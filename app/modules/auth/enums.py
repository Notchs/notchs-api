from enum import StrEnum


class SubjectType(StrEnum):
    """Who a token or session belongs to. Values match `auth_tokens.subject_type`."""

    USER = "user"  # a customer
    VENUE_MEMBER = "venue_member"
    ADMIN = "admin"


class TokenType(StrEnum):
    """What a stored token is for. Values match `auth_tokens.token_type`."""

    MAGIC_LINK = "magic_link"  # customer login link sent by the WhatsApp bot
    REFRESH = "refresh"
    SET_PASSWORD = "set_password"
    PASSWORD_RESET = "password_reset"
    DEVICE_PAIRING = "device_pairing"


class OtpPurpose(StrEnum):
    """What an emailed 6-digit code is for. Codes live in Redis, not in auth_tokens."""

    APPLICATION = "application"
    EMAIL_CHANGE = "email_change"


class AccountStatus(StrEnum):
    """State of a password login (`admin_users.status`, `venue_members.status`)."""

    INVITED = "invited"  # added, the emailed set-password link not used yet
    ACTIVE = "active"
    DISABLED = "disabled"


class RevokeReason(StrEnum):
    """Why a stored token was cancelled (`auth_tokens.revoke_reason`, set with `revoked_at`).
    A refresh that rotates a token marks it used, not revoked."""

    LOGOUT = "logout"
    SUPERSEDED = "superseded"  # a newer link of the same kind was sent
    REUSE_DETECTED = "reuse_detected"  # a rotated refresh token came back: stolen cookie
    PASSWORD_CHANGED = "password_changed"
    ACCOUNT_DISABLED = "account_disabled"  # turned off, blocked or deleted
    ADMIN = "admin"  # removed by the team
