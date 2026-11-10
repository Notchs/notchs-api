"""Bill-photo claims, for a Notch the counter phone could not give (see models.py).

    website   POST /me/claims {venue_code} -> CLM- code and a WhatsApp link pre-typed with
              "CLAIM CLM-7K3M9QX2"; up to 3 a day, one per place a day, only for live places
              where the customer has no Notch today (and none of that kind)
    WhatsApp  "CLAIM CLM-..." -> "now send the photo"; the photo (the same day in India) is
              attached to their newest waiting claim, and the claim goes to review
    admin     sees the photo (fetched from WhatsApp on request, never stored by Notchs),
              approves (a normal visit with points, marked `verified`) or rejects with a reason

Approval checks the daily limits again, because the customer may have scanned elsewhere since.
The customer is told on WhatsApp only while the reply is free: within 24 hours of their own last
message. Otherwise they see the result in their passport. No paid templates.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from urllib.parse import quote

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import Media, WhatsAppClient, WhatsAppMediaError, download_media
from app.common.dependencies import PaginationParams
from app.common.time import IST, ist_date
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.admin_users.models import AdminUser
from app.modules.claims.exceptions import (
    ClaimAlreadyNotchedException,
    ClaimCannotApproveException,
    ClaimExistsException,
    ClaimLimitException,
    ClaimNicheLimitException,
    ClaimNotFoundException,
    ClaimNotInReviewException,
    ClaimPhotoFetchFailedException,
    ClaimPhotoUnavailableException,
    ClaimVenueNotFoundException,
)
from app.modules.claims.models import ClaimStatus, VisitClaim
from app.modules.claims.repository import ClaimRepository
from app.modules.claims.schemas import (
    AdminClaimOut,
    ClaimDecisionOut,
    ClaimStartOut,
    ClaimVenueOut,
    MyClaimOut,
)
from app.modules.leaderboards.service import LeaderboardService
from app.modules.scans.models import ScanSession, ScanStatus, VerificationLevel
from app.modules.scans.niches import niche_of
from app.modules.scans.service import ScanService
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import Venue
from app.modules.whatsapp.models import MessageStatus, WhatsAppMessage
from app.modules.whatsapp.sender import WhatsAppSender

logger = get_logger(__name__)

COMMAND = "CLAIM"
_COMMAND = re.compile(r"^\s*CLAIM\s+(CLM-[0-9A-Z]{8})\s*$", re.IGNORECASE)
MAX_CLAIMS_PER_DAY = 3
MY_CLAIMS_SHOWN = 20
# WhatsApp keeps media about 30 days; a day less, to be safe.
PHOTO_KEPT_FOR = timedelta(days=29)
# Replies are free for 24 hours after the customer's own message; a little less, to be safe.
FREE_REPLY_WINDOW = timedelta(hours=23, minutes=50)
_MEDIA_ID = re.compile(r"[0-9A-Za-z_-]{1,128}")

MediaFetcher = Callable[[str], Awaitable[Media]]


def parse_command(text: str | None) -> str | None:
    """The claim code in "CLAIM CLM-7K3M9QX2", uppercased, or None if the message isn't one."""
    match = _COMMAND.match(text or "")
    return match.group(1).upper() if match else None


def end_of_day(day: date) -> datetime:
    """Midnight in India at the end of `day`."""
    return datetime.combine(day + timedelta(days=1), time(), IST).astimezone(UTC)


def whatsapp_link(claim_code: str) -> str | None:
    number = settings.WHATSAPP_PUBLIC_NUMBER
    if not number:
        return None
    return f"https://wa.me/{number}?text={quote(f'{COMMAND} {claim_code}', safe='')}"


def photo_media_id(message: WhatsAppMessage) -> str | None:
    """WhatsApp's id for the image in a stored inbound message, if it looks like one."""
    raw = (message.payload or {}).get("message")
    image = raw.get("image") if isinstance(raw, dict) else None
    media_id = image.get("id") if isinstance(image, dict) else None
    return media_id if isinstance(media_id, str) and _MEDIA_ID.fullmatch(media_id) else None


class PhotoOutcome(StrEnum):
    ATTACHED = "attached"
    NO_CLAIM = "no_claim"  # no claim waiting for a photo today
    NOT_A_PHOTO = "not_a_photo"  # an image message without a usable id


class PromptOutcome(StrEnum):
    SEND_PHOTO = "send_photo"  # the claim is waiting for its photo
    IN_REVIEW = "in_review"  # the photo already came
    UNKNOWN = "unknown"  # not theirs, decided, or its day has passed


@dataclass(frozen=True, slots=True)
class BotResult[OutcomeT]:
    outcome: OutcomeT
    venue_name: str | None = None


class ClaimService:
    def __init__(
        self,
        session: AsyncSession,
        redis: Redis,
        media_fetcher: MediaFetcher = download_media,
    ) -> None:
        self.session = session
        self.redis = redis
        self.repository = ClaimRepository(session)
        self.fetch_media = media_fetcher

    # The customer, on the website.

    async def search_venues(self, query: str) -> list[ClaimVenueOut]:
        return [
            ClaimVenueOut(
                venue_code=venue.venue_code,
                name=venue.name,
                area=area,
                niche=niche_of(venue.category).label,
            )
            for venue, area in await self.repository.live_venues(query)
        ]

    async def start(
        self, user: User, venue_code: str, now: datetime | None = None
    ) -> ClaimStartOut:
        now = now or datetime.now(UTC)
        today = ist_date(now)
        venue = await self.repository.live_venue(venue_code)
        if venue is None:
            raise ClaimVenueNotFoundException()
        scans = ScanService(self.session, self.redis)
        # One at a time per customer, so the daily limit holds.
        user = await scans.repository.lock_customer(user.id)

        existing = await self.repository.live_claim(user.id, venue.id, today)
        if existing is not None and existing.status == ClaimStatus.AWAITING_PHOTO:
            return self._started(existing, venue, today)
        if existing is not None:
            raise ClaimExistsException()
        if await scans.repository.has_visit_on(user.id, venue.id, today):
            raise ClaimAlreadyNotchedException()
        if await scans.repository.has_niche_visit_on(user.id, venue.category, today):
            raise ClaimNicheLimitException(
                f"You already have a {niche_of(venue.category).label} Notch today: "
                "one per kind each day."
            )
        if await self.repository.count_started_on(user.id, today) >= MAX_CLAIMS_PER_DAY:
            raise ClaimLimitException()

        claim = VisitClaim(user_id=user.id, venue_id=venue.id, visit_date=today)
        try:
            async with self.session.begin_nested():
                self.session.add(claim)
        except IntegrityError:  # unreachable while the customer row is locked; kept as a guard
            raise ClaimExistsException() from None
        logger.info("Claim %s started at venue %s", claim.claim_code, venue.venue_code)
        return self._started(claim, venue, today)

    @staticmethod
    def _started(claim: VisitClaim, venue: Venue, today: date) -> ClaimStartOut:
        return ClaimStartOut(
            claim_code=claim.claim_code,
            venue_name=venue.name,
            message=f"{COMMAND} {claim.claim_code}",
            whatsapp_link=whatsapp_link(claim.claim_code),
            expires_at=end_of_day(today),
        )

    async def mine(self, user: User, now: datetime | None = None) -> list[MyClaimOut]:
        today = ist_date(now or datetime.now(UTC))
        claims = []
        for claim, venue_name, notch_no in await self.repository.my_claims(
            user.id, MY_CLAIMS_SHOWN
        ):
            status = claim.status
            if status == ClaimStatus.AWAITING_PHOTO and claim.visit_date < today:
                status = "expired"
            claims.append(
                MyClaimOut(
                    claim_code=claim.claim_code,
                    venue_name=venue_name,
                    visit_date=claim.visit_date,
                    status=status,
                    notch_no=notch_no,
                    reject_reason=claim.reject_reason,
                    created_at=claim.created_at,
                )
            )
        return claims

    # The customer, on WhatsApp (inside the bot's transaction).

    async def prompt(self, user: User, claim_code: str, now: datetime) -> BotResult[PromptOutcome]:
        """The answer to "CLAIM CLM-...", sent before the photo."""
        claim = await self.repository.claim_of(user.id, claim_code)
        if claim is None or claim.visit_date != ist_date(now):
            return BotResult(PromptOutcome.UNKNOWN)
        venue = await self.session.get(Venue, claim.venue_id)
        name = venue.name if venue else None
        if claim.status == ClaimStatus.AWAITING_PHOTO:
            return BotResult(PromptOutcome.SEND_PHOTO, name)
        if claim.status == ClaimStatus.IN_REVIEW:
            return BotResult(PromptOutcome.IN_REVIEW, name)
        return BotResult(PromptOutcome.UNKNOWN)

    async def attach_photo(self, user: User, message: WhatsAppMessage) -> BotResult[PhotoOutcome]:
        """A photo the customer sent goes to their newest claim waiting for one, that day. A
        caption with a claim code picks that claim."""
        media_id = photo_media_id(message)
        if media_id is None:
            return BotResult(PhotoOutcome.NOT_A_PHOTO)
        received = message.created_at
        claim = await self.repository.lock_awaiting(
            user.id, ist_date(received), parse_command(message.body)
        )
        if claim is None:
            return BotResult(PhotoOutcome.NO_CLAIM)
        claim.photo_message_id = message.id
        claim.photo_media_id = media_id
        claim.photo_received_at = received
        claim.status = ClaimStatus.IN_REVIEW
        await self.session.flush()
        venue = await self.session.get(Venue, claim.venue_id)
        logger.info("Claim %s: photo received", claim.claim_code)
        return BotResult(PhotoOutcome.ATTACHED, venue.name if venue else None)

    # The admin.

    async def list(
        self, status: ClaimStatus | None, pagination: PaginationParams
    ) -> tuple[list[AdminClaimOut], int]:
        rows, total = await self.repository.admin_list(status, pagination.offset, pagination.size)
        return await self._admin_out(rows), total

    async def get(self, claim_code: str) -> AdminClaimOut:
        row = await self.repository.admin_row(claim_code)
        if row is None:
            raise ClaimNotFoundException()
        return (await self._admin_out([row]))[0]

    async def photo(self, claim_code: str, now: datetime | None = None) -> Media:
        now = now or datetime.now(UTC)
        row = await self.repository.admin_row(claim_code)
        if row is None:
            raise ClaimNotFoundException()
        claim: VisitClaim = row[0]
        if claim.photo_media_id is None or claim.photo_received_at is None:
            raise ClaimPhotoUnavailableException("No photo has arrived for this claim yet.")
        if now - claim.photo_received_at > PHOTO_KEPT_FOR:
            raise ClaimPhotoUnavailableException()
        try:
            return await self.fetch_media(claim.photo_media_id)
        except WhatsAppMediaError as exc:
            logger.warning("Claim %s: photo fetch failed (%s)", claim.claim_code, exc.code)
            if exc.code == "gone":
                raise ClaimPhotoUnavailableException() from None
            if exc.code == "not_configured":
                raise ClaimPhotoUnavailableException(
                    "Photos can't be fetched while WhatsApp is in dry-run mode."
                ) from None
            raise ClaimPhotoFetchFailedException() from None

    async def approve(
        self, claim_code: str, admin: AdminUser, client: WhatsAppClient, now: datetime | None = None
    ) -> ClaimDecisionOut:
        now = now or datetime.now(UTC)
        claim = await self._lock_in_review(claim_code)
        scans = ScanService(self.session, self.redis)
        # Customer, then venue: the scan pipeline's lock order.
        user = await scans.repository.lock_customer(claim.user_id)
        venue = await self.session.get(Venue, claim.venue_id)
        assert venue is not None  # RESTRICT foreign key
        if user.status != UserStatus.ACTIVE or user.deleted_at is not None:
            raise ClaimCannotApproveException("The customer's account is blocked or deleted.")
        if await scans.repository.has_visit_on(user.id, venue.id, claim.visit_date):
            raise ClaimCannotApproveException("They already have a Notch here that day.")
        if await scans.repository.has_niche_visit_on(user.id, venue.category, claim.visit_date):
            raise ClaimCannotApproveException(
                f"They already have a {niche_of(venue.category).label} Notch that day."
            )
        if claim.photo_message_id is None or claim.photo_received_at is None:
            raise ClaimCannotApproveException("The photo's WhatsApp message is missing.")
        await scans.repository.lock_venue(venue.id)

        # The photo's message is the scan: verified, with no code and no counter phone.
        scan_session = ScanSession(
            user_id=user.id,
            venue_id=venue.id,
            inbound_message_id=claim.photo_message_id,
            status=ScanStatus.VERIFIED,
            resolved_at=now,
        )
        self.session.add(scan_session)
        await self.session.flush()
        visit, points = await scans.add_visit(
            user,
            venue,
            scan_session,
            visited_at=claim.photo_received_at,
            slot=None,
            level=VerificationLevel.VERIFIED,
        )
        claim.status = ClaimStatus.APPROVED
        claim.visit_id = visit.id
        claim.reviewed_by_admin_id = admin.id
        claim.reviewed_at = now
        await self.session.commit()
        logger.info("Claim %s approved by admin %s", claim.claim_code, admin.admin_user_code)

        try:
            await LeaderboardService(self.redis).sync_user(self.session, user.id)
        except RedisError:  # the next rebuild puts the points on the board
            logger.exception("Claim %s: points not on the leaderboard yet", claim.claim_code)
        notified = await self._tell(
            client,
            user,
            f"Good news! We checked your bill from {venue.name}: Notch "
            f"#{visit.notch_no:04d} added, +{points} points.",
            now,
        )
        return ClaimDecisionOut(claim=await self.get(claim.claim_code), customer_notified=notified)

    async def reject(
        self,
        claim_code: str,
        admin: AdminUser,
        reason: str,
        client: WhatsAppClient,
        now: datetime | None = None,
    ) -> ClaimDecisionOut:
        now = now or datetime.now(UTC)
        claim = await self._lock_in_review(claim_code)
        claim.status = ClaimStatus.REJECTED
        claim.reject_reason = reason
        claim.reviewed_by_admin_id = admin.id
        claim.reviewed_at = now
        await self.session.commit()
        logger.info("Claim %s rejected by admin %s", claim.claim_code, admin.admin_user_code)

        user = await self.session.get(User, claim.user_id)
        venue = await self.session.get(Venue, claim.venue_id)
        notified = False
        if user is not None and venue is not None and user.status == UserStatus.ACTIVE:
            notified = await self._tell(
                client,
                user,
                f"We couldn't accept your bill from {venue.name}: {reason}",
                now,
            )
        return ClaimDecisionOut(claim=await self.get(claim.claim_code), customer_notified=notified)

    async def _lock_in_review(self, claim_code: str) -> VisitClaim:
        claim = await self.repository.lock_by_code(claim_code)
        if claim is None:
            raise ClaimNotFoundException()
        if claim.status != ClaimStatus.IN_REVIEW:
            raise ClaimNotInReviewException()
        return claim

    async def _tell(self, client: WhatsAppClient, user: User, text: str, now: datetime) -> bool:
        """A WhatsApp message, only while it is a free reply to the customer's own message."""
        last = await self.repository.last_message_from(user.phone_e164)
        if last is None or now - last > FREE_REPLY_WINDOW:
            return False
        message = await WhatsAppSender(self.session, client).send_text(
            user.phone_e164, text, user_id=user.id
        )
        await self.session.commit()
        return message.status != MessageStatus.FAILED

    async def _admin_out(self, rows: list[Row] | tuple[Row, ...]) -> list[AdminClaimOut]:
        now = datetime.now(UTC)
        last_seen = await self.repository.counters_last_seen(list({row[1].id for row in rows}))
        return [
            AdminClaimOut(
                claim_code=claim.claim_code,
                status=claim.status,
                venue_name=venue.name,
                venue_code=venue.venue_code,
                niche=niche_of(venue.category).label,
                customer_handle=handle,
                visit_date=claim.visit_date,
                created_at=claim.created_at,
                photo_received_at=claim.photo_received_at,
                photo_available=claim.photo_media_id is not None
                and claim.photo_received_at is not None
                and now - claim.photo_received_at <= PHOTO_KEPT_FOR,
                counter_last_seen_at=last_seen.get(venue.id),
                reviewed_by=reviewer,
                reviewed_at=claim.reviewed_at,
                reject_reason=claim.reject_reason,
                notch_no=notch_no,
            )
            for claim, venue, handle, reviewer, notch_no in rows
        ]
