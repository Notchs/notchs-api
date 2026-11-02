"""What the bot does with one incoming WhatsApp message (Core Loop B1-B10).

    every message      find the customer by phone, or create them (their first message is
                       sign-up). Blocked and deleted customers get no reply.
    counter scan       "Hey Notchs!, get me notched. KAHWA-482913" (notches/codes.py):
                       Notched (B2), already today (B3), expired (B4); the first ever scan asks
                       for consent first (B1). Then Founding Member (B8, once) and new badges (B6).
    I agree            records 18+ and privacy consent, then notches the scan that was waiting
    Privacy notice     the privacy page link
    LOGIN, My passport, See badges, See my card
                       a single-use passport login link (3 per 15 minutes), landing on the page
                       the button names
    HELP               what the bot understands (B9)
    DELETE, then YES   closes the account; a job wipes the data within a minute (B9)
    MY DATA            a login link to the passport's data page, where the download is (B9)
    anything else      "I only understand scans" (B10), at most once every 10 minutes

The processor calls the bot inside a transaction that holds a lock on the message, and marks the
message done afterwards. If the bot raises, nothing is kept and the message is retried, so any
reply sent before the error could be sent again: sending replies is the last thing it does.
All replies answer the customer's own message, so they fall inside WhatsApp's free 24-hour window.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import WhatsAppClient
from app.common.phone import masked_phone
from app.core.logging import get_logger
from app.modules.auth.customer_auth import LINK_WINDOW_SECONDS, CustomerAuthService
from app.modules.auth.enums import TokenType
from app.modules.auth.exceptions import AuthRateLimitedException, TokenInvalidException
from app.modules.auth.tokens import lifetime
from app.modules.notches.checkin import CheckInResult, CheckInService, Outcome
from app.modules.scans.parser import NoCode, parse
from app.modules.users.consent import record_privacy_consent
from app.modules.users.deletion import DeletionService
from app.modules.users.handles import clean_name, free_handle
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository
from app.modules.whatsapp import replies
from app.modules.whatsapp.models import MessageType, WhatsAppMessage
from app.modules.whatsapp.replies import Reply
from app.modules.whatsapp.sender import WhatsAppSender
from app.modules.whatsapp.webhook import button_id_of

logger = get_logger(__name__)

LOGIN_COMMAND = "LOGIN"
HELP_COMMAND = "HELP"
DELETE_COMMAND = "DELETE"
YES_COMMAND = "YES"
MY_DATA_COMMAND = "MY DATA"
AGREE_COMMANDS = frozenset({"I AGREE", "AGREE"})

# Buttons that answer with a login link, and the passport page each one lands on.
LINK_BUTTONS: dict[str, str | None] = {
    replies.LOGIN.id: None,
    replies.PASSPORT.id: None,
    replies.SEE_BADGES.id: "/badges",
    replies.SEE_CARD.id: "/cards/latest",
}
MY_DATA_PATH = "/me/privacy"

# "Anything else" and "too many links" replies: each at most once per customer per window, so
# nobody is flooded. Separate windows: one never silences the other.
NOTICE_EVERY_SECONDS = 10 * 60
NOT_UNDERSTOOD_NOTICE = "help"
TOO_MANY_LINKS_NOTICE = "too_many_links"

LINK_MINUTES = int(lifetime(TokenType.MAGIC_LINK).total_seconds() // 60)
# A WhatsApp timestamp further ahead than this is not trusted (the server's clock wins).
MAX_CLOCK_AHEAD = timedelta(minutes=5)


def _notice_key(notice: str, user: User) -> str:
    return f"wa:notice:{notice}:{user.id}"


def command_of(message: WhatsAppMessage) -> str | None:
    """The text, trimmed, upper case, single spaces: "my  data " -> "MY DATA". A template's quick
    reply button arrives with its title as the text, so it counts too."""
    if message.message_type not in (MessageType.TEXT, MessageType.BUTTON) or not message.body:
        return None
    return " ".join(message.body.split()).upper()


def sent_at(message: WhatsAppMessage) -> datetime:
    """When the customer sent the message: WhatsApp's own timestamp, so a message handled late
    (a retry after a restart) is judged by when it was sent, not when we got to it."""
    now = datetime.now(UTC)
    payload = message.payload if isinstance(message.payload, dict) else {}
    meta = payload.get("message")
    raw = meta.get("timestamp") if isinstance(meta, dict) else None
    try:
        moment = datetime.fromtimestamp(int(raw), UTC)
    except TypeError, ValueError, OverflowError:
        return message.created_at or now
    return now if moment > now + MAX_CLOCK_AHEAD else moment


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

        outbox = await self._handle(session, user, message)
        sender = WhatsAppSender(session, self.client)
        for reply in outbox:
            if reply.buttons:
                await sender.send_buttons(
                    user.phone_e164, reply.text, reply.buttons, user_id=user.id
                )
            else:
                await sender.send_text(user.phone_e164, reply.text, user_id=user.id)

    async def _handle(
        self, session: AsyncSession, user: User, message: WhatsAppMessage
    ) -> list[Reply]:
        """Decide what to do and do it; returns the replies to send, in order."""
        command = command_of(message)
        button = (
            button_id_of(message.payload) if message.message_type == MessageType.BUTTON else None
        )

        deletion = DeletionService(session)
        if command == YES_COMMAND and await deletion.is_waiting_for_yes(user):
            return [replies.deleted()] if await deletion.confirm(user) else []
        if (
            button == replies.AGREE.id
            or command in AGREE_COMMANDS
            or (command == YES_COMMAND and user.consent_at is None)
        ):
            accepted_with = "button" if button else f"typed {command}"
            return await self._consent(session, user, accepted_with)
        if button == replies.PRIVACY.id:
            return [replies.privacy_notice()]

        scan = parse(message.body) if message.message_type == MessageType.TEXT else None
        if isinstance(scan, NoCode):
            return [replies.no_code()]
        if scan is not None:
            result = await CheckInService(session, self.redis).scan(
                user, scan, scanned_at=sent_at(message), whatsapp_message_id=message.id
            )
            return self._scan_replies(result)

        if button in LINK_BUTTONS:
            return await self._link(session, user, LINK_BUTTONS[button], replies.login_link)
        if command == LOGIN_COMMAND:
            return await self._link(session, user, None, replies.login_link)
        if command == MY_DATA_COMMAND:
            return await self._link(session, user, MY_DATA_PATH, replies.my_data_link)
        if button == replies.HELP.id or command == HELP_COMMAND:
            return [replies.help_menu()]
        if command == DELETE_COMMAND:
            await deletion.ask(user)
            return [replies.delete_confirm()]
        if await self._notice_allowed(NOT_UNDERSTOOD_NOTICE, user):
            return [replies.not_understood()]
        return []

    async def _consent(self, session: AsyncSession, user: User, accepted_with: str) -> list[Reply]:
        if user.consent_at is None:
            await record_privacy_consent(
                session, user, at=datetime.now(UTC), accepted_with=accepted_with
            )
        result = await CheckInService(session, self.redis).complete_pending(user)
        if result is None:
            return [replies.consent_thanks()]
        return self._scan_replies(result)

    @staticmethod
    def _scan_replies(result: CheckInResult) -> list[Reply]:
        venue_name = result.venue.name if result.venue else "this place"
        match result.outcome:
            case Outcome.NOTCHED:
                assert result.notch is not None
                out = [replies.notched(venue_name, result.notch.notch_no, result.visits_this_month)]
                if result.announce_founding_no is not None:
                    out.append(replies.founding_member(result.announce_founding_no))
                if result.new_badges:
                    out.append(replies.new_badges(result.new_badges))
                return out
            case Outcome.ALREADY_TODAY:
                return [replies.already_today(venue_name)]
            case Outcome.NEEDS_CONSENT:
                return [replies.welcome_consent()]
            case Outcome.VENUE_NOT_LIVE:
                return [replies.venue_not_live(venue_name)]
            case Outcome.DAILY_LIMIT:
                return [replies.daily_limit()]
            case _:
                return [replies.code_expired()]

    async def _link(
        self,
        session: AsyncSession,
        user: User,
        next_path: str | None,
        make_reply: Callable[[str, int], Reply],
    ) -> list[Reply]:
        try:
            link = await CustomerAuthService(session, self.redis).issue_login_link(user, next_path)
        except AuthRateLimitedException:
            if await self._notice_allowed(TOO_MANY_LINKS_NOTICE, user):
                return [replies.too_many_links(LINK_WINDOW_SECONDS // 60)]
            return []
        except TokenInvalidException:  # blocked or deleted since we looked: no link
            return []
        return [make_reply(link, LINK_MINUTES)]

    async def _notice_allowed(self, notice: str, user: User) -> bool:
        """True at most once per NOTICE_EVERY_SECONDS for this notice and customer."""
        key = _notice_key(notice, user)
        return bool(await self.redis.set(key, "1", ex=NOTICE_EVERY_SECONDS, nx=True))
