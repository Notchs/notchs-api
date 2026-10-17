"""What the bot does with one incoming WhatsApp message.

    every message   find the customer by phone, or create them (their first message is sign-up)
    LOGIN           reply with a single-use passport login link (3 per 15 minutes); typed, or a
                    tap on the help reply's "Login link" button
    anything else   a short help reply with that button, at most once every 10 minutes per customer

Blocked and deleted customers get no reply. Check-in codes (`NOTCH KAHWA 482913`) get the help
reply for now; stamping comes with the check-ins module.

The processor calls the bot inside a transaction that holds a lock on the message, and marks the
message done afterwards. If the bot raises, nothing is kept and the message is retried, so any
reply sent before the error could be sent again: sending a reply is the last thing it does.
All replies answer the customer's own message, so they fall inside WhatsApp's free 24-hour window.
"""

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
from app.modules.users.models import User, UserStatus
from app.modules.whatsapp.models import MessageType, WhatsAppMessage
from app.modules.whatsapp.payloads import ReplyButton
from app.modules.whatsapp.sender import WhatsAppSender
from app.modules.whatsapp.webhook import button_id_of

logger = get_logger(__name__)

LOGIN_COMMAND = "LOGIN"
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


def _notice_key(notice: str, user: User) -> str:
    return f"wa:notice:{notice}:{user.id}"


def is_login(message: WhatsAppMessage) -> bool:
    if message.message_type == MessageType.BUTTON and (
        button_id_of(message.payload) == LOGIN_BUTTON.id
    ):
        return True
    return (
        message.message_type in (MessageType.TEXT, MessageType.BUTTON)
        and (message.body or "").strip().upper() == LOGIN_COMMAND
    )


async def find_or_create_customer(session: AsyncSession, phone_e164: str) -> User:
    """The customer with this phone number, created if new. Two first messages arriving at
    once still make one customer (the phone number is unique)."""
    await session.execute(
        insert(User)
        .values(phone_e164=phone_e164)
        .on_conflict_do_nothing(index_elements=[User.phone_e164])
    )
    user = await session.scalar(select(User).where(User.phone_e164 == phone_e164))
    assert user is not None
    return user


class WhatsAppBot:
    def __init__(self, redis: Redis, client: WhatsAppClient) -> None:
        self.redis = redis
        self.client = client

    async def __call__(self, session: AsyncSession, message: WhatsAppMessage) -> None:
        user = await find_or_create_customer(session, message.phone_e164)
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
        elif await self._notice_allowed(HELP_NOTICE, user):
            await sender.send_buttons(user.phone_e164, HELP_REPLY, [LOGIN_BUTTON], user_id=user.id)

    async def _login(self, session: AsyncSession, sender: WhatsAppSender, user: User) -> None:
        try:
            link = await CustomerAuthService(session, self.redis).issue_login_link(user)
        except AuthRateLimitedException:
            if await self._notice_allowed(TOO_MANY_LINKS_NOTICE, user):
                await sender.send_text(user.phone_e164, TOO_MANY_LINKS_REPLY, user_id=user.id)
            return
        except TokenInvalidException:  # blocked or deleted since we looked: no link
            return
        await sender.send_text(user.phone_e164, LOGIN_REPLY.format(link=link), user_id=user.id)

    async def _notice_allowed(self, notice: str, user: User) -> bool:
        """True at most once per NOTICE_EVERY_SECONDS for this notice and customer."""
        key = _notice_key(notice, user)
        return bool(await self.redis.set(key, "1", ex=NOTICE_EVERY_SECONDS, nx=True))
