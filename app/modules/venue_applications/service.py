"""The "apply as a venue" form: verify the email with a code, then submit.

Emails are returned, not sent: the router sends them after the response.
"""

from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailMessage
from app.core import rate_limit
from app.core.config import settings
from app.modules.auth import messages as auth_messages
from app.modules.auth.emails import is_disposable, normalize_email
from app.modules.auth.enums import OtpPurpose
from app.modules.auth.exceptions import EmailNotAllowedException
from app.modules.auth.otp import EmailCodes
from app.modules.locations.models import City
from app.modules.venue_applications import messages
from app.modules.venue_applications.exceptions import (
    ApplicationCityInvalidException,
    ApplicationDuplicateException,
    ApplicationRateLimitedException,
    VerificationExpiredException,
)
from app.modules.venue_applications.models import ApplicationStatus, VenueApplication
from app.modules.venue_applications.schemas import ApplicationCreate
from app.modules.venues.models import VenueMember

MAX_APPLICATIONS_PER_IP_PER_DAY = 5
DAY = 24 * 60 * 60
CODE_PURPOSE_TEXT = "verify your email for your Notchs venue application"


class VenueApplicationService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.codes = EmailCodes(redis)
        self.redis = redis

    async def send_code(self, email: str, *, ip: str | None) -> EmailMessage:
        """Code for the application form. Sent whether or not the address is already in use,
        so this cannot be used to find out who is registered (submit refuses duplicates)."""
        email = normalize_email(email)
        if is_disposable(email):
            raise EmailNotAllowedException()
        code = await self.codes.issue(OtpPurpose.APPLICATION, email, ip=ip or "unknown")
        return auth_messages.email_code_email(to=email, code=code, purpose=CODE_PURPOSE_TEXT)

    async def verify_code(self, email: str, code: str) -> str:
        """Check the code and return the verification token the form submits with."""
        await self.codes.verify(OtpPurpose.APPLICATION, email, code)
        return await self.codes.issue_verification_token(email)

    async def submit(
        self, form: ApplicationCreate, *, ip: str | None
    ) -> tuple[VenueApplication, EmailMessage | None]:
        """Save a pending application. Returns it and the team's heads-up email, if one is set.

        The verification token is only used up once the application is saved, so a rejected
        submit (a typo in the form, say) can be fixed and sent again.
        """
        email = await self.codes.peek_verification_token(form.verification_token)
        if email is None:
            raise VerificationExpiredException()
        if not await rate_limit.hit(
            self.redis,
            f"rl:apply:ip:{ip or 'unknown'}",
            limit=MAX_APPLICATIONS_PER_IP_PER_DAY,
            window_seconds=DAY,
        ):
            raise ApplicationRateLimitedException()

        city = await self.session.get(City, form.city_id)
        if city is None or not city.is_active:
            raise ApplicationCityInvalidException()
        if await self._email_taken(email):
            raise ApplicationDuplicateException()

        application = VenueApplication(
            **form.model_dump(exclude={"verification_token", "phone"}),
            phone_e164=form.phone,
            email=email,
            email_verified_at=datetime.now(UTC),
        )
        self.session.add(application)
        try:
            # The partial unique index is the final guard against two submits at once.
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ApplicationDuplicateException() from exc

        await self.codes.consume_verification_token(form.verification_token)
        notice = (
            messages.new_application_email(
                to=settings.ADMIN_NOTIFY_EMAIL, application=application, city=city.name
            )
            if settings.ADMIN_NOTIFY_EMAIL
            else None
        )
        return application, notice

    async def _email_taken(self, email: str) -> bool:
        """A pending application or an existing venue login already uses this address."""
        pending = exists().where(
            VenueApplication.email == email,
            VenueApplication.status == ApplicationStatus.PENDING,
        )
        member = exists().where(VenueMember.email == email)
        return bool(await self.session.scalar(select(pending | member)))
