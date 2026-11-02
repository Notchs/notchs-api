"""Every reply the bot sends, in one place (Page Inventory §2, Core Loop B1-B10).

Max 2 short lines and 2 buttons, bold only the key word (*word*), no emoji in the copy.
Button ids come back in the webhook when tapped; titles are at most 20 characters.
"""

from dataclasses import dataclass, field

from app.core.config import settings
from app.modules.notches.badges import BADGES, REGULAR_VISITS
from app.modules.notches.models import Badge
from app.modules.users.consent import PRIVACY_CONSENT_ACCEPT, PRIVACY_CONSENT_TEXT
from app.modules.users.models import MAX_FOUNDING_MEMBER_NO
from app.modules.whatsapp.payloads import ReplyButton

# Buttons. The ids are what the bot reads back; keep them stable.
PASSPORT = ReplyButton(id="passport", title="My passport")
SEE_BADGES = ReplyButton(id="badges", title="See badges")
SEE_CARD = ReplyButton(id="card", title="See my card")
HELP = ReplyButton(id="help", title="Help")
LOGIN = ReplyButton(id="login", title="🔑 Login link")
AGREE = ReplyButton(id="consent_yes", title=PRIVACY_CONSENT_ACCEPT)
PRIVACY = ReplyButton(id="privacy", title="Privacy notice")


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    buttons: list[ReplyButton] = field(default_factory=list)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


# B1: first ever scan. Consent once, in WhatsApp; not repeated in the passport.
def welcome_consent() -> Reply:
    # The stored consent quotes these exact words (users/consent.py).
    return Reply(PRIVACY_CONSENT_TEXT, [AGREE, PRIVACY])


def privacy_notice() -> Reply:
    return Reply(
        f"Our privacy notice, in plain words: {settings.CUSTOMER_APP_URL}/privacy\n"
        "Tap *I agree* when you're ready.",
        [AGREE],
    )


def consent_thanks() -> Reply:
    """Agreed, but no scan was waiting (or it waited too long)."""
    return Reply("*You're in.* Scan the screen at any Notchs counter to get your first Notch.")


# B2: visit verified. The second line is the one next step.
def notched(venue_name: str, notch_no: int, visits_this_month: int) -> Reply:
    first = f"*Notched.* {venue_name} · Notch #{notch_no:04d}"
    left = REGULAR_VISITS - visits_this_month
    if left > 0:
        second = f"{_plural(left, 'more visit')} to become the Regular here."
    elif left == 0:
        second = "You're the Regular here this month."
    else:
        second = ""
    return Reply(f"{first}\n{second}" if second else first, [PASSPORT])


# B3: same place, same day.
def already_today(venue_name: str) -> Reply:
    return Reply(f"*Already notched* at {venue_name} today. Come back tomorrow.", [PASSPORT])


# B4: old or wrong code.
def code_expired() -> Reply:
    return Reply("This *code expired*. Scan the screen at the counter again.")


def venue_not_live(venue_name: str) -> Reply:
    return Reply(f"*{venue_name}* isn't taking Notchs right now. Try again another day.")


# B6: badge or milestone, sent right after the Notch that earns it.
def new_badges(badges: list[Badge]) -> Reply:
    if len(badges) == 1:
        info = BADGES[badges[0]]
        return Reply(f"New badge: *{info.name}.* {info.how}", [SEE_BADGES])
    names = [BADGES[b].name for b in badges]
    listed = ", ".join(names[:-1]) + f" and {names[-1]}"
    return Reply(f"New badges: *{listed}.*", [SEE_BADGES])


# B8: joined among the first 500. Real number only, sent once (with the first Notch).
def founding_member(number: int) -> Reply:
    return Reply(f"You're Founding Member *#{number}* of {MAX_FOUNDING_MEMBER_NO}.", [PASSPORT])


# B9: HELP / DELETE / MY DATA / LOGIN.
def help_menu() -> Reply:
    return Reply(
        "Send *LOGIN* for your passport link, *MY DATA* for a copy of your data, or *DELETE* to "
        "remove everything.",
        [LOGIN],
    )


def login_link(link: str, minutes: int) -> Reply:
    return Reply(
        f"Here is your Notchs passport login link. It works once and expires in {minutes} "
        f"minutes:\n{link}"
    )


def my_data_link(link: str, minutes: int) -> Reply:
    return Reply(
        "Open this link to see your data and download a copy. It works once and expires in "
        f"{minutes} minutes:\n{link}"
    )


def too_many_links(minutes: int) -> Reply:
    return Reply(f"You have asked for a lot of links. Please try again in {minutes} minutes.")


def delete_confirm() -> Reply:
    return Reply("*Delete everything?* Reply YES.")


def deleted() -> Reply:
    return Reply("*Done.* Everything is deleted.")


# B10: anything else typed. Points to HELP; no chatbot small talk.
def not_understood() -> Reply:
    return Reply("I only understand scans. Type *HELP* for options.", [HELP])
