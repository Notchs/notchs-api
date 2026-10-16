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
