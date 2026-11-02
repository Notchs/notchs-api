"""Outgoing email through Resend (https://resend.com/docs/api-reference/emails/send-email).

With EMAIL_DRY_RUN=true (the default for local work) nothing is sent: the email is written to
the log instead, including any code or link in it.

With SMTP_HOST set (local and development only) emails go to that server instead of Resend. It is
for Mailpit (docker-compose.yml), which keeps them in a web inbox and delivers nothing.
"""

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

RESEND_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailSendError(Exception):
    """Resend or the SMTP server refused the email or could not be reached."""


class EmailClient:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        # Tests pass a client with a mock transport. Otherwise one client is made per send:
        # login emails are rare, so a shared connection pool is not worth the lifecycle.
        self._http = http

    async def send(self, message: EmailMessage) -> None:
        if settings.EMAIL_DRY_RUN:
            logger.info(
                "EMAIL (dry run) to=%s subject=%r\n%s", message.to, message.subject, message.text
            )
            return
        if settings.SMTP_HOST:
            await asyncio.to_thread(_send_smtp, message)
            logger.info("Email sent to %s through SMTP (%s)", message.to, message.subject)
            return
        if settings.RESEND_API_KEY is None:
            raise EmailSendError("RESEND_API_KEY is not set")

        payload: dict[str, object] = {
            "from": settings.EMAIL_FROM,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html is not None:
            payload["html"] = message.html
        headers = {"Authorization": f"Bearer {settings.RESEND_API_KEY.get_secret_value()}"}

        try:
            if self._http is not None:
                response = await self._http.post(RESEND_URL, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
                    response = await http.post(RESEND_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise EmailSendError(f"Could not reach Resend: {exc}") from exc

        if response.is_error:
            # The body names the problem (bad key, unverified domain, ...) without the content.
            raise EmailSendError(f"Resend returned {response.status_code}: {response.text}")
        logger.info("Email sent to %s (%s)", message.to, message.subject)


def _send_smtp(message: EmailMessage) -> None:
    mime = MimeMessage()
    mime["From"] = settings.EMAIL_FROM
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime.set_content(message.text)
    if message.html is not None:
        mime.add_alternative(message.html, subtype="html")
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=TIMEOUT_SECONDS) as smtp:
            smtp.send_message(mime)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailSendError(f"Could not send through SMTP: {exc}") from exc


def get_email_client() -> EmailClient:
    """FastAPI dependency. Override it in tests."""
    return EmailClient()
