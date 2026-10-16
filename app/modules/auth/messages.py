"""The emails auth sends. Plain text for now; HTML versions can come with the brand templates."""

from urllib.parse import urlencode

from app.clients.email import EmailMessage
from app.core.config import settings
from app.modules.auth.enums import TokenType
from app.modules.auth.tokens import lifetime


def _hours(token_type: TokenType) -> str:
    minutes = int(lifetime(token_type).total_seconds() // 60)
    return f"{minutes // 60} hours" if minutes >= 120 else f"{minutes} minutes"


def email_code_email(*, to: str, code: str, purpose: str) -> EmailMessage:
    """The 6-digit code that proves someone owns an address. `purpose` completes the sentence
    "Use this code to ...", e.g. "verify your email for your Notchs venue application"."""
    return EmailMessage(
        to=to,
        subject=f"Your Notchs code: {code}",
        text=(
            f"Your Notchs code is {code}\n\n"
            f"Use this code to {purpose}. It expires in 10 minutes.\n\n"
            "If you didn't ask for it, ignore this email.\n\n— Team Notchs"
        ),
    )


def set_password_link(app_url: str, raw_token: str) -> str:
    """One page per app serves both the first password and a reset; it POSTs the token back."""
    return f"{app_url}/set-password?{urlencode({'token': raw_token})}"


def venue_welcome_email(*, to: str, name: str, venue_name: str, raw_token: str) -> EmailMessage:
    link = set_password_link(settings.VENUE_APP_URL, raw_token)
    return EmailMessage(
        to=to,
        subject=f"Welcome to Notchs, {venue_name}! Set your password",
        text=(
            f"Hi {name},\n\n"
            f"{venue_name} is approved on Notchs. Set your password to open your venue "
            f"dashboard:\n\n{link}\n\n"
            f"This link works once and expires in {_hours(TokenType.SET_PASSWORD)}. "
            f"Your login email is {to}.\n\n— Team Notchs"
        ),
    )


def admin_invite_email(*, to: str, name: str, raw_token: str) -> EmailMessage:
    link = set_password_link(settings.ADMIN_APP_URL, raw_token)
    return EmailMessage(
        to=to,
        subject="Your Notchs admin account: set your password",
        text=(
            f"Hi {name},\n\n"
            f"An admin account was created for you. Set your password here:\n\n{link}\n\n"
            f"This link works once and expires in {_hours(TokenType.SET_PASSWORD)}.\n\n"
            "— Team Notchs"
        ),
    )


def password_changed_email(*, to: str, name: str, app_url: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Your Notchs password was changed",
        text=(
            f"Hi {name},\n\n"
            "The password for your Notchs login was just changed, and every other device "
            "was logged out.\n\n"
            f"If this wasn't you, reset your password now: {app_url}/forgot-password\n\n"
            "— Team Notchs"
        ),
    )


def email_changed_email(*, to: str, name: str, new_email: str) -> EmailMessage:
    """Sent to the OLD address, so the owner notices if someone else changed it."""
    return EmailMessage(
        to=to,
        subject="Your Notchs login email was changed",
        text=(
            f"Hi {name},\n\n"
            f"The login email for your Notchs venue account was changed to {new_email}. "
            "From now on, log in with that address.\n\n"
            "If this wasn't you, reply to this email right away.\n\n— Team Notchs"
        ),
    )


def password_reset_email(*, to: str, name: str, raw_token: str, app_url: str) -> EmailMessage:
    link = set_password_link(app_url, raw_token)
    return EmailMessage(
        to=to,
        subject="Reset your Notchs password",
        text=(
            f"Hi {name},\n\n"
            f"Someone asked to reset the password for {to}. If it was you, choose a new "
            f"password here:\n\n{link}\n\n"
            f"This link works once and expires in {_hours(TokenType.PASSWORD_RESET)}. "
            f"If it wasn't you, ignore this email; your password stays the same.\n\n— Team Notchs"
        ),
    )
