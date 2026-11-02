from datetime import date, datetime

from pydantic import Field

from app.common.schemas import BaseSchema
from app.modules.cards.models import CardFace
from app.modules.venues.schemas import VenueBriefOut


class FaceOut(BaseSchema):
    """One face of the card, word for word (Page 3, Faces A, B, C)."""

    face: CardFace
    kicker: str  # "TOP OF AHMEDABAD"
    hero: str  # the one Volt number: "8%"
    unit: str  # "Late-night café hopper"
    proof: str | None  # "23 Notchs in October"
    ember: str  # "Unnotched: 203 places in Ahmedabad."


class RevealScreenOut(BaseSchema):
    """One full screen of the 1st-of-the-month reveal (C9)."""

    kicker: str
    hero: str
    rest: str
    sub: str | None


class CardTopPlaceOut(BaseSchema):
    name: str
    visits: int


class CardOut(BaseSchema):
    card_code: str  # NC-1026-0142
    month: date  # first day of the month
    month_label: str  # "October"
    city: str
    name: str | None
    notch_count: int
    places_count: int
    top_place: CardTopPlaceOut | None
    regular_since: date | None
    vibe: str | None
    avg_time: str | None
    city_rank: int | None
    rank_percent: int | None
    unnotched_count: int
    # Fewer than 3 Notchs: one face, no reveal.
    is_simple: bool
    # The face to show and share; `faces` lists every face "Change look" can pick.
    face: CardFace
    faces: list[FaceOut]
    reveal: list[RevealScreenOut]
    revealed: bool
    shared: bool
    # Where the shared card opens (A12); None while private.
    share_url: str | None


class CardListItemOut(BaseSchema):
    """A card in the past cards grid (C10)."""

    card_code: str
    month: date
    month_label: str
    notch_count: int
    face: CardFace
    revealed: bool


class CardUpdateIn(BaseSchema):
    face: CardFace | None = None
    # Send true once the reveal has been watched (or skipped).
    revealed: bool | None = None


class PublicCardOut(BaseSchema):
    """The shared card page (A12). Only what the customer chose to share: first name, the card,
    and the places they notched that month."""

    card_code: str
    first_name: str | None
    month_label: str
    year: int
    city: str
    # "Riya is in the top 8% of Ahmedabad.", or a plainer line without a rank.
    headline: str
    face: FaceOut
    # "Her places you haven't notched" (up to 3; the viewer's own Notchs left out).
    places: list[VenueBriefOut] = Field(default_factory=list)
    shared_at: datetime
