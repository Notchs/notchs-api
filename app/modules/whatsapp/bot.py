"""What the bot does with one incoming WhatsApp message.

    CHANGE 123456   from a new number: move the customer's account to it (users/phone_change.py);
                    handled before sign-up, so the new number never gets an account of its own
    every message   find the customer by phone, or create them (their first message is sign-up)
    LOGIN           reply with a single-use passport login link (3 per 15 minutes); typed, the
                    website's pre-typed "Hey Notchs, I want to login", or a tap on the help
                    reply's "Login link" button
    "I agree" tap   record the privacy consent and 18+ confirmation, reply with a login link
    a scan          count the visit or refuse it (scans module), then reply; "KAHWA-482913"
    menu tap        save what they tried, for 24 hours after the visit; no points
    no code         the scan sentence from a QR away from a venue: no stamp, a login link
    anything else   a short help reply with that button, at most once every 10 minutes per customer

Customers who have not agreed yet never get a link: they get the consent question instead, and
the link when they tap. A scan still counts first, because its code expires fast. They get the
"What did you try?" menu only once they have agreed.

Blocked and deleted customers get no reply.

The processor calls the bot inside a transaction that holds a lock on the message, and marks the
message done afterwards. If the bot raises, nothing is kept and the message is retried, so any
reply sent before the error could be sent again: sending a reply is the last thing it does.
A scan is different: its result and "message done" are committed before the reply, so the venue
is not locked during the call to Meta. A crash in that gap loses the reply but never counts or
replies twice, and scanning again answers "already notched today".
All replies answer the customer's own message, so they fall inside WhatsApp's free 24-hour window.
"""

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import WhatsAppClient
from app.common.phone import masked_phone
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.auth.customer_auth import LINK_WINDOW_SECONDS, CustomerAuthService
from app.modules.auth.enums import TokenType
from app.modules.auth.exceptions import AuthRateLimitedException, TokenInvalidException
from app.modules.auth.tokens import lifetime
from app.modules.leaderboards.service import LeaderboardService, Period
from app.modules.locations.models import City
from app.modules.scans import replies
from app.modules.scans.niches import niche_of
from app.modules.scans.parser import NoCode, Scan, parse
from app.modules.scans.service import ScanResult, ScanService
from app.modules.tags.models import Tag
from app.modules.tags.repository import TagRepository
from app.modules.tags.service import TagService, TriedOutcome
from app.modules.users.consent import CONSENT_VERSION, consent_text
from app.modules.users.handles import clean_name, free_handle
from app.modules.users.models import User, UserStatus
from app.modules.users.phone_change import PhoneChangeOutcome, PhoneChangeService, parse_command
from app.modules.users.repository import UserRepository
from app.modules.venues.models import Venue
from app.modules.whatsapp.models import MessageType, WhatsAppMessage
from app.modules.whatsapp.payloads import MAX_LIST_ROW_TITLE_LENGTH, ListRow, ReplyButton
from app.modules.whatsapp.sender import WhatsAppSender
from app.modules.whatsapp.webhook import button_id_of

logger = get_logger(__name__)

LOGIN_COMMAND = "LOGIN"
# What the website's "Continue with WhatsApp" button pre-types (wa.me/<number>?text=...).
LOGIN_MESSAGE = "Hey Notchs, I want to login"
# The same sentence however it was punctuated or capitalised, with or without the greeting.
_LOGIN_MESSAGE = re.compile(r"(?:(?:hey|hi|hello) )?notchs i want to log ?in")
# Help and "too many links" replies: each at most once per customer per window, so nobody is
# flooded. Separate windows: a help reply never silences the "too many links" answer.
NOTICE_EVERY_SECONDS = 10 * 60
HELP_NOTICE = "help"
TOO_MANY_LINKS_NOTICE = "too_many_links"

LINK_MINUTES = int(lifetime(TokenType.MAGIC_LINK).total_seconds() // 60)
LOGIN_REPLY = (
    "Here is your Notchs passport login link. It works once and expires in "
    f"{LINK_MINUTES} minutes:\n{{link}}"
)
TOO_MANY_LINKS_REPLY = (
    "You have asked for a lot of login links. "
    f"Please try again in {LINK_WINDOW_SECONDS // 60} minutes."
)
HELP_REPLY = (
    f"Hi from Notchs! Tap the button to get your passport login link, or send {LOGIN_COMMAND}."
)
# The help reply's button. Its id comes back when the customer taps it.
LOGIN_BUTTON = ReplyButton(id="login", title="🔑 Login link")
CONSENT_BUTTON = ReplyButton(id="consent_agree", title="I agree, I'm 18+")
CONSENT_THANKS = "Thanks!"
# The same refused-scan or no-code reply at most once a minute per customer.
SCAN_NOTICE_EVERY_SECONDS = 60
PHONE_CHANGE_REPLIES = {
    PhoneChangeOutcome.CHANGED: (
        "Done. This is now your Notchs number. Your Notchs and rank moved with you."
    ),
    PhoneChangeOutcome.SAME: "This is already your Notchs number.",
    PhoneChangeOutcome.INVALID: (
        "That code didn't work. Codes work once and expire after 10 minutes. "
        "Get a new one in your Notchs passport."
    ),
    PhoneChangeOutcome.TAKEN: (
        "This number already has its own Notchs account, so another one can't move onto it."
    ),
    PhoneChangeOutcome.LIMITED: "Too many tries. Please wait 15 minutes and try again.",
}
# A menu row's id: try:<visit id>:<tag id>, so tapping an old menu updates the right visit.
TRY_ROW_PREFIX = "try"


def _notice_key(notice: str, user: User) -> str:
    return f"wa:notice:{notice}:{user.id}"


def try_row_id(visit_id: uuid.UUID, tag_id: uuid.UUID) -> str:
    return f"{TRY_ROW_PREFIX}:{visit_id}:{tag_id}"


def try_rows(visit_id: uuid.UUID, items: Sequence[Tag]) -> list[ListRow]:
    """Tag names can be longer than WhatsApp allows in a menu row."""
    limit = MAX_LIST_ROW_TITLE_LENGTH
    return [
        ListRow(
            id=try_row_id(visit_id, tag.id),
            title=tag.name if len(tag.name) <= limit else f"{tag.name[: limit - 1]}…",
        )
        for tag in items
    ]


def parse_try_row(message: WhatsAppMessage) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(visit id, tag id) if this is a tap on the menu."""
    if message.message_type != MessageType.BUTTON:
        return None
    prefix, _, ids = (button_id_of(message.payload) or "").partition(":")
    visit_id, _, tag_id = ids.partition(":")
    if prefix != TRY_ROW_PREFIX:
        return None
    try:
        return uuid.UUID(visit_id), uuid.UUID(tag_id)
    except ValueError:
        return None


def is_consent(message: WhatsAppMessage) -> bool:
    return (
        message.message_type == MessageType.BUTTON
        and button_id_of(message.payload) == CONSENT_BUTTON.id
    )


def is_login(message: WhatsAppMessage) -> bool:
    if message.message_type == MessageType.BUTTON and (
        button_id_of(message.payload) == LOGIN_BUTTON.id
    ):
        return True
    if message.message_type not in (MessageType.TEXT, MessageType.BUTTON):
        return False
    body = message.body or ""
    if body.strip().upper() == LOGIN_COMMAND:
        return True
    words = re.sub(r"[^a-z0-9]+", " ", body.lower()).strip()
    return _LOGIN_MESSAGE.fullmatch(words) is not None


async def find_or_create_customer(
    session: AsyncSession, phone_e164: str, profile_name: str | None = None
) -> User:
    """The customer with this phone number, created if new with their WhatsApp profile name and
    a handle made from it (riya.m4821). Two first messages arriving at once still make one
    customer (the phone number is unique). A customer without a name gets one from a later
    message; a name already set (perhaps changed on the website) is never overwritten."""
    name = clean_name(profile_name)
    await session.execute(
        insert(User)
        .values(
            phone_e164=phone_e164,
            name=name,
            handle=await free_handle(name, taken=UserRepository(session).handle_exists),
        )
        .on_conflict_do_nothing(index_elements=[User.phone_e164])
    )
    user = await session.scalar(select(User).where(User.phone_e164 == phone_e164))
    assert user is not None
    if user.name is None and name is not None:
        user.name = name
    return user


class WhatsAppBot:
    def __init__(self, redis: Redis, client: WhatsAppClient) -> None:
        self.redis = redis
        self.client = client

    async def __call__(self, session: AsyncSession, message: WhatsAppMessage) -> None:
        code = parse_command(message.body) if message.message_type == MessageType.TEXT else None
        if code is not None:
            await self._phone_change(session, message, code)
            return
        user = await find_or_create_customer(
            session, message.phone_e164, (message.payload or {}).get("profile_name")
        )
        message.user_id = user.id
        if user.status != UserStatus.ACTIVE or user.deleted_at is not None:
            logger.info(
                "WhatsApp message from %s customer %s: no reply",
                user.status,
                masked_phone(user.phone_e164),
            )
            return

        sender = WhatsAppSender(session, self.client)
        if is_login(message):
            await self._login(session, sender, user)
            return
        if is_consent(message):
            await self._consent(session, sender, user)
            return
        tried = parse_try_row(message)
        if tried is not None:
            await self._tried(session, sender, user, *tried)
            return
        scan = parse(message.body) if message.message_type == MessageType.TEXT else None
        if isinstance(scan, Scan):
            await self._scan(session, sender, user, message, scan)
        elif isinstance(scan, NoCode):
            await self._no_code(session, sender, user)
        elif await self._notice_allowed(HELP_NOTICE, user):
            await sender.send_buttons(user.phone_e164, HELP_REPLY, [LOGIN_BUTTON], user_id=user.id)

    async def _phone_change(
        self, session: AsyncSession, message: WhatsAppMessage, code: str
    ) -> None:
        outcome, user = await PhoneChangeService(session, self.redis).finish(
            message.phone_e164, code
        )
        if outcome in (PhoneChangeOutcome.CHANGED, PhoneChangeOutcome.SAME):
            assert user is not None
            message.user_id = user.id
        else:
            message.user_id = await session.scalar(
                select(User.id).where(User.phone_e164 == message.phone_e164)
            )
            # A wrong code from a number with a blocked or deleted account: no reply.
            if message.user_id is not None:
                owner = await session.get(User, message.user_id)
                if owner is None or owner.status != UserStatus.ACTIVE:
                    return
            notice = f"wa:notice:phone_change:{message.phone_e164}"
            if not await self.redis.set(notice, "1", ex=SCAN_NOTICE_EVERY_SECONDS, nx=True):
                return
        text = PHONE_CHANGE_REPLIES[outcome]
        await WhatsAppSender(session, self.client).send_text(
            message.phone_e164, text, user_id=message.user_id
        )

    async def _login(self, session: AsyncSession, sender: WhatsAppSender, user: User) -> None:
        if user.consent_at is None:
            if await self._notice_allowed("consent", user, SCAN_NOTICE_EVERY_SECONDS):
                await sender.send_buttons(
                    user.phone_e164, consent_text(), [CONSENT_BUTTON], user_id=user.id
                )
            return
        try:
            link = await CustomerAuthService(session, self.redis).issue_login_link(user)
        except AuthRateLimitedException:
            if await self._notice_allowed(TOO_MANY_LINKS_NOTICE, user):
                await sender.send_text(user.phone_e164, TOO_MANY_LINKS_REPLY, user_id=user.id)
            return
        except TokenInvalidException:  # blocked or deleted since we looked: no link
            return
        await sender.send_text(user.phone_e164, LOGIN_REPLY.format(link=link), user_id=user.id)

    async def _scan(
        self,
        session: AsyncSession,
        sender: WhatsAppSender,
        user: User,
        message: WhatsAppMessage,
        scan: Scan,
    ) -> None:
        result = await ScanService(session, self.redis).record_scan(user, message.id, scan)
        message.processed_at = datetime.now(UTC)
        await session.commit()  # before the reply: see the top of this file

        venue_id = result.scan_session.venue_id
        venue = await session.get(Venue, venue_id) if venue_id else None
        if result.verified:
            assert venue is not None  # a counted visit always has one
            await self._stamp(session, sender, user, result, venue)
            return
        assert result.reject_reason is not None
        text = replies.rejection_text(
            result.reject_reason,
            venue.name if venue else None,
            niche_of(venue.category).label if venue else None,
        )
        if text is not None and await self._notice_allowed(
            f"scan_{result.reject_reason.value}", user, SCAN_NOTICE_EVERY_SECONDS
        ):
            await sender.send_text(user.phone_e164, text, user_id=user.id)

    async def _stamp(
        self,
        session: AsyncSession,
        sender: WhatsAppSender,
        user: User,
        result: ScanResult,
        venue: Venue,
    ) -> None:
        assert result.visit is not None
        agreed = user.consent_at is not None
        link = await self._link_if_possible(session, user) if agreed else None
        rank_line = await self._add_to_leaderboard(session, user, result, venue)
        text = replies.stamp_text(
            venue.name, result.visit.notch_no, result.points_earned, link, rank_line, agreed=agreed
        )
        image = settings.WHATSAPP_STAMP_IMAGE_URL
        if image:
            await sender.send_image(user.phone_e164, image, text, user_id=user.id)
        else:
            await sender.send_text(user.phone_e164, text, user_id=user.id)
        if not agreed:
            await sender.send_buttons(
                user.phone_e164, consent_text(), [CONSENT_BUTTON], user_id=user.id
            )
            return
        items = await TagRepository(session).venue_items(venue.id)
        if items:
            await sender.send_list(
                user.phone_e164,
                replies.TRY_QUESTION,
                replies.TRY_BUTTON,
                try_rows(result.visit.id, items),
                user_id=user.id,
            )

    async def _add_to_leaderboard(
        self, session: AsyncSession, user: User, result: ScanResult, venue: Venue
    ) -> str | None:
        """The visit is already saved, so Redis failing only costs the rank line. The next rebuild
        puts the points back on the board."""
        assert result.visit is not None
        service = LeaderboardService(self.redis)
        try:
            await service.add_points(
                venue.city_id, user.id, result.points_earned, result.visit.visit_date
            )
            rank = await service.rank_of(venue.city_id, user.id, Period.MONTH)
        except RedisError:
            logger.exception("Visit %s not on the leaderboard: Redis failed", result.visit.id)
            return None
        city = await session.get(City, venue.city_id)
        return replies.rank_text(rank.rank, city.name) if rank and city else None

    async def _tried(
        self,
        session: AsyncSession,
        sender: WhatsAppSender,
        user: User,
        visit_id: uuid.UUID,
        tag_id: uuid.UUID,
    ) -> None:
        result = await TagService(session).record_tried(user, visit_id, tag_id)
        match result.outcome:
            case TriedOutcome.SAVED:
                assert result.tag is not None
                text = replies.tried_text(result.tag.name)
            case TriedOutcome.ALREADY_SAVED:  # a double tap: already answered
                return
            case TriedOutcome.NOT_ON_MENU:
                text = replies.TRY_NOT_ON_MENU
            case TriedOutcome.VISIT_CLOSED:
                text = replies.TRY_VISIT_CLOSED
        if result.outcome == TriedOutcome.SAVED or await self._notice_allowed(
            f"try_{result.outcome.value}", user, SCAN_NOTICE_EVERY_SECONDS
        ):
            await sender.send_text(user.phone_e164, text, user_id=user.id)

    async def _no_code(self, session: AsyncSession, sender: WhatsAppSender, user: User) -> None:
        if not await self._notice_allowed("no_code", user, SCAN_NOTICE_EVERY_SECONDS):
            return
        if user.consent_at is None:
            await sender.send_buttons(
                user.phone_e164,
                f"{replies.SCAN_AT_VENUE}\n\n{consent_text()}",
                [CONSENT_BUTTON],
                user_id=user.id,
            )
            return
        link = await self._link_if_possible(session, user)
        await sender.send_text(user.phone_e164, replies.no_code_text(link), user_id=user.id)

    async def _consent(self, session: AsyncSession, sender: WhatsAppSender, user: User) -> None:
        recorded = await UserRepository(session).record_consent(
            user.id, CONSENT_VERSION, datetime.now(UTC)
        )
        if not recorded:  # a second tap: already answered
            return
        await session.refresh(user)
        link = await self._link_if_possible(session, user)
        text = f"{CONSENT_THANKS} {replies.no_code_text(link)}"
        await sender.send_text(user.phone_e164, text, user_id=user.id)

    async def _link_if_possible(self, session: AsyncSession, user: User) -> str | None:
        """A login link, or None rather than losing the reply it goes in."""
        try:
            return await CustomerAuthService(session, self.redis).issue_login_link(user)
        except AuthRateLimitedException, TokenInvalidException:
            return None
        except RedisError:
            logger.exception("No login link for customer %s: Redis failed", user.id)
            return None

    async def _notice_allowed(
        self, notice: str, user: User, seconds: int = NOTICE_EVERY_SECONDS
    ) -> bool:
        """True at most once per `seconds` for this notice and customer."""
        key = _notice_key(notice, user)
        return bool(await self.redis.set(key, "1", ex=seconds, nx=True))
