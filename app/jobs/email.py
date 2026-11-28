"""Every email goes through the job queue (user rule, 3 Nov 2026): a request only queues it, and
the worker sends it, with retries.

If Redis does not answer when queuing, the email is sent at once instead, so it is not lost.
"""

from dataclasses import asdict
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.clients.email import EmailClient, EmailMessage, EmailSendError
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.jobs.queue import enqueue
from app.jobs.worker import PermanentJobError

logger = get_logger(__name__)

SEND_EMAIL = "send_email"


async def send_email_job(payload: dict[str, Any], job_id: str) -> None:
    try:
        message = EmailMessage(**payload)
    except TypeError as exc:
        raise PermanentJobError("not an email") from exc
    # Resend sends one email per key, so a retry after a lost answer never sends it twice.
    await EmailClient().send(message, idempotency_key=f"{settings.ENVIRONMENT}-job-{job_id}")


class EmailQueue:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def enqueue(self, message: EmailMessage) -> None:
        """Queue the email. Never raises: a failure is logged (never the address or text)."""
        try:
            job_id = await enqueue(self.redis, SEND_EMAIL, asdict(message))
        except RedisError:
            logger.warning("Email queue unavailable (Redis did not answer); sending it now")
            try:
                await EmailClient().send(message)
            except EmailSendError:
                logger.exception("An email could neither be queued nor sent")
            return
        logger.info("Email queued as job %s", job_id)


def get_email_queue() -> EmailQueue:
    """FastAPI dependency. Override it in tests."""
    return EmailQueue(redis_client)
