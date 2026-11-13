"""Admin Settings → System: whether email, WhatsApp and the bot check are really switched on,
and a test email. Nothing here is a secret: only on/off states, never keys or tokens.

It exists because a half-configured deploy fails silently: with EMAIL_DRY_RUN on, every login
link is only written to the log, and with WHATSAPP_DRY_RUN on the bot's replies are too.
"""

from typing import Literal

from redis.asyncio import Redis

from app.clients.email import EmailClient, EmailMessage, EmailSendError
from app.common.exceptions import AppException, TooManyRequestsException
from app.common.schemas import BaseSchema
from app.core import rate_limit
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.admin_users.models import AdminUser

logger = get_logger(__name__)

TEST_EMAILS_PER_WINDOW = 5
WINDOW_SECONDS = 15 * 60

EmailMode = Literal["sending", "dry_run", "local_smtp", "not_configured"]
WhatsAppMode = Literal["sending", "dry_run", "not_configured"]
BotCheckMode = Literal["on", "off", "not_configured"]


class SystemStatusOut(BaseSchema):
    environment: str
    version: str
    # "dry_run": emails are written to the server log, not sent.
    email: EmailMode
    email_from: str
    # "dry_run": the bot's replies are written to the server log, not sent.
    whatsapp: WhatsAppMode
    # False until Meta can call the webhook (app secret and verify token are set).
    whatsapp_webhook_ready: bool
    whatsapp_number_set: bool
    bot_check: BotCheckMode
    # True when the website and the API share PROXY_SECRET, so a visitor's address (for rate
    # limits) can't be made up.
    proxy_secret_set: bool
    # Where links in emails and WhatsApp replies point.
    customer_app_url: str
    venue_app_url: str
    admin_app_url: str


class TestEmailOut(BaseSchema):
    sent_to: str
    mode: EmailMode


class TestEmailFailedException(AppException):
    status_code = 502
    error_code = "admin.test_email_failed"
    message = "The email provider refused the test email."


class TestEmailLimitException(TooManyRequestsException):
    error_code = "admin.test_email_limit"
    message = "Too many test emails. Please try again in 15 minutes."


def email_mode() -> EmailMode:
    if settings.EMAIL_DRY_RUN:
        return "dry_run"
    if settings.SMTP_HOST:
        return "local_smtp"
    return "sending" if settings.RESEND_API_KEY is not None else "not_configured"


def system_status() -> SystemStatusOut:
    whatsapp: WhatsAppMode = "dry_run"
    if not settings.WHATSAPP_DRY_RUN:
        configured = settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID
        whatsapp = "sending" if configured else "not_configured"
    bot_check: BotCheckMode = "off"
    if not settings.TURNSTILE_DISABLED:
        bot_check = "on" if settings.TURNSTILE_SECRET is not None else "not_configured"
    return SystemStatusOut(
        environment=settings.ENVIRONMENT,
        version=settings.APP_VERSION,
        email=email_mode(),
        email_from=settings.EMAIL_FROM,
        whatsapp=whatsapp,
        whatsapp_webhook_ready=bool(
            settings.WHATSAPP_APP_SECRET and settings.WHATSAPP_VERIFY_TOKEN
        ),
        whatsapp_number_set=bool(settings.WHATSAPP_PUBLIC_NUMBER),
        bot_check=bot_check,
        proxy_secret_set=settings.PROXY_SECRET is not None,
        customer_app_url=settings.CUSTOMER_APP_URL,
        venue_app_url=settings.VENUE_APP_URL,
        admin_app_url=settings.ADMIN_APP_URL,
    )


async def send_test_email(admin: AdminUser, redis: Redis, client: EmailClient) -> TestEmailOut:
    """A test email to the admin's own address, sent now, so a failure shows its reason."""
    if not await rate_limit.hit(
        redis,
        f"rl:admin_test_email:{admin.id}",
        limit=TEST_EMAILS_PER_WINDOW,
        window_seconds=WINDOW_SECONDS,
    ):
        raise TestEmailLimitException()
    message = EmailMessage(
        to=admin.email,
        subject="Notchs test email",
        text=(
            "This is a test email from the Notchs admin panel.\n"
            "If you can read it, Notchs can send emails."
        ),
    )
    try:
        await client.send(message)
    except EmailSendError as exc:
        logger.warning("Test email by admin %s failed: %s", admin.admin_user_code, exc)
        # The provider's own words (bad key, unverified domain...); they hold no secret.
        raise TestEmailFailedException(str(exc)[:500]) from exc
    return TestEmailOut(sent_to=admin.email, mode=email_mode())
