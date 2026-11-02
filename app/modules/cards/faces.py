"""The words on a card: faces A, B, C, the simple card, and the four reveal screens (C9).

Kept on the server so the app, the share images and the public page all say the same thing.
"""

from datetime import timedelta

from app.modules.cards.models import CardFace, NotchCard
from app.modules.cards.schemas import FaceOut, RevealScreenOut
from app.modules.locations.time import month_label, month_of
from app.modules.notches.badges import REGULAR_VISITS


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def ember(card: NotchCard, city_name: str) -> str:
    return f"Unnotched: {card.unnotched_count} places in {city_name}."


def faces(card: NotchCard, city_name: str) -> list[FaceOut]:
    """Every face this card can show. Face C needs a rank; the simple card has one face."""
    month = month_label(card.period)
    line = ember(card, city_name)
    count_unit = f"{_plural(card.notch_count, 'Notch')} in {month}"
    if card.is_simple:
        next_month = month_label(month_of(card.period).end + timedelta(days=1))
        return [
            FaceOut(
                face=CardFace.COUNT,
                kicker=f"{_plural(card.notch_count, 'NOTCH').upper()} THIS MONTH",
                hero=str(card.notch_count),
                unit=f"{count_unit}.",
                proof=f"{next_month} is yours.",
                ember=line,
            )
        ]
    out = [
        FaceOut(
            face=CardFace.COUNT,
            kicker="NOTCHS THIS MONTH",
            hero=str(card.notch_count),
            unit=count_unit,
            proof=f"Your place: {card.top_venue_name}" if card.top_venue_name else None,
            ember=line,
        ),
        FaceOut(
            face=CardFace.PLACE,
            kicker="YOUR PLACE",
            hero=str(card.top_venue_visits),
            unit=f"{_plural(card.top_venue_visits, 'visit')} to {card.top_venue_name}",
            proof=(
                f"Regular since {card.regular_since.day} {card.regular_since.strftime('%b')}"
                if card.regular_since
                else f"{card.places_count} {_plural(card.places_count, 'place')} in {month}"
            ),
            ember=line,
        ),
    ]
    if card.rank_percent is not None:
        out.append(
            FaceOut(
                face=CardFace.RANK,
                kicker=f"TOP OF {city_name.upper()}",
                hero=f"{card.rank_percent}%",
                unit=card.vibe or "",
                proof=f"{card.notch_count} Notchs in {month}",
                ember=line,
            )
        )
    elif card.city_rank is not None:
        out.append(
            FaceOut(
                face=CardFace.RANK,
                kicker=f"IN {city_name.upper()}",
                hero=f"#{card.city_rank}",
                unit=card.vibe or "",
                proof=f"{card.notch_count} Notchs in {month}",
                ember=line,
            )
        )
    return out


def reveal(card: NotchCard, city_name: str) -> list[RevealScreenOut]:
    """The 1st-of-the-month reveal: one fact per screen, the peak last. None for simple cards."""
    if card.is_simple:
        return []
    month = month_label(card.period)
    screens = [
        RevealScreenOut(
            kicker=month.upper(),
            hero=str(card.notch_count),
            rest=f"Notchs in {month}",
            sub="Every one a real visit.",
        ),
        RevealScreenOut(
            kicker="YOUR PLACE",
            hero=str(card.top_venue_visits),
            rest=f"{_plural(card.top_venue_visits, 'visit')} to {card.top_venue_name}",
            sub="You're the Regular there." if card.top_venue_visits >= REGULAR_VISITS else None,
        ),
        RevealScreenOut(
            kicker="YOUR VIBE",
            hero=card.avg_time or "",
            rest=card.vibe or "",
            sub="Your average Notch time.",
        ),
    ]
    if card.rank_percent is not None:
        screens.append(
            RevealScreenOut(
                kicker="THE PEAK",
                hero=f"{card.rank_percent}%",
                rest=f"Top {card.rank_percent}% of {city_name}",
                sub=f"Out of everyone who notched in {month}.",
            )
        )
    elif card.city_rank is not None:
        screens.append(
            RevealScreenOut(
                kicker="THE PEAK",
                hero=f"#{card.city_rank}",
                rest=f"#{card.city_rank} in {city_name}",
                sub=f"Out of everyone who notched in {month}.",
            )
        )
    return screens
