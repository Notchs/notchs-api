"""Reading and sharing monthly Notch Cards: the customer's own (C9, C10) and shared ones (A12)."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.cards import faces as words
from app.modules.cards.exceptions import (
    CardFaceUnavailableException,
    CardNotFoundException,
    CardNotSharedException,
)
from app.modules.cards.models import NotchCard
from app.modules.cards.schemas import (
    CardListItemOut,
    CardOut,
    CardTopPlaceOut,
    CardUpdateIn,
    PublicCardOut,
)
from app.modules.locations.models import City
from app.modules.locations.time import month_label, month_of
from app.modules.scans.models import COUNTED_STATUSES, Visit
from app.modules.users.display import first_name
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import Venue, VenueStatus
from app.modules.venues.page import Places
from app.modules.venues.schemas import VenueBriefOut

SHARED_PLACES = 3


def share_url(card: NotchCard) -> str:
    return f"{settings.CUSTOMER_APP_URL}/card/{card.card_code}"


def card_out(card: NotchCard, city_name: str) -> CardOut:
    return CardOut(
        card_code=card.card_code,
        month=card.period,
        month_label=month_label(card.period),
        city=city_name,
        name=card.name,
        notch_count=card.notch_count,
        places_count=card.places_count,
        top_place=(
            CardTopPlaceOut(name=card.top_venue_name, visits=card.top_venue_visits)
            if card.top_venue_name
            else None
        ),
        regular_since=card.regular_since,
        vibe=card.vibe,
        avg_time=card.avg_time,
        city_rank=card.city_rank,
        rank_percent=card.rank_percent,
        unnotched_count=card.unnotched_count,
        is_simple=card.is_simple,
        face=card.face,
        faces=words.faces(card, city_name),
        reveal=words.reveal(card, city_name),
        revealed=card.revealed_at is not None,
        shared=card.shared_at is not None,
        share_url=share_url(card) if card.shared_at else None,
    )


def headline(card: NotchCard, name: str | None, city_name: str) -> str:
    who = first_name(name) or "This explorer"
    month = month_label(card.period)
    if card.rank_percent is not None:
        return f"{who} is in the top {card.rank_percent}% of {city_name}."
    if card.city_rank is not None:
        return f"{who} is #{card.city_rank} in {city_name} for {month}."
    unit = "Notch" if card.notch_count == 1 else "Notchs"
    return f"{who} got {card.notch_count} {unit} in {month}."


class CardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, user: User) -> list[CardListItemOut]:
        cards = await self.session.scalars(
            select(NotchCard).where(NotchCard.user_id == user.id).order_by(NotchCard.period.desc())
        )
        return [
            CardListItemOut(
                card_code=card.card_code,
                month=card.period,
                month_label=month_label(card.period),
                notch_count=card.notch_count,
                face=card.face,
                revealed=card.revealed_at is not None,
            )
            for card in cards
        ]

    async def latest(self, user: User) -> CardOut:
        card = await self.session.scalar(
            select(NotchCard)
            .where(NotchCard.user_id == user.id)
            .order_by(NotchCard.period.desc())
            .limit(1)
        )
        if card is None:
            raise CardNotFoundException()
        return card_out(card, await self._city_name(card))

    async def get(self, user: User, code: str) -> CardOut:
        card = await self._own(user, code)
        return card_out(card, await self._city_name(card))

    async def update(self, user: User, code: str, body: CardUpdateIn) -> CardOut:
        card = await self._own(user, code)
        city_name = await self._city_name(card)
        if body.face is not None:
            if body.face not in {f.face for f in words.faces(card, city_name)}:
                raise CardFaceUnavailableException()
            card.face = body.face
        if body.revealed and card.revealed_at is None:
            card.revealed_at = datetime.now(UTC)
        await self.session.flush()
        return card_out(card, city_name)

    async def set_shared(self, user: User, code: str, shared: bool) -> CardOut:
        """Share makes /card/<code> work for anyone; unshare turns it off again (A12)."""
        card = await self._own(user, code)
        if shared and card.shared_at is None:
            card.shared_at = datetime.now(UTC)
        elif not shared:
            card.shared_at = None
        await self.session.flush()
        return card_out(card, await self._city_name(card))

    async def public(self, code: str, viewer: User | None) -> PublicCardOut:
        row = (
            await self.session.execute(
                select(NotchCard, User.name)
                .join(User, User.id == NotchCard.user_id)
                .where(
                    NotchCard.card_code == code.strip().upper(),
                    NotchCard.shared_at.is_not(None),
                    User.status == UserStatus.ACTIVE,
                )
            )
        ).one_or_none()
        if row is None:
            raise CardNotSharedException()
        card, name = row
        city_name = await self._city_name(card)
        shown = next(
            (f for f in words.faces(card, city_name) if f.face == card.face),
            words.faces(card, city_name)[0],
        )
        return PublicCardOut(
            card_code=card.card_code,
            first_name=first_name(card.name or name),
            month_label=month_label(card.period),
            year=card.period.year,
            city=city_name,
            headline=headline(card, card.name or name, city_name),
            face=shown,
            places=await self._places_for(card, viewer),
            shared_at=card.shared_at,
        )

    async def _places_for(self, card: NotchCard, viewer: User | None) -> list[VenueBriefOut]:
        """The sharer's places that month, most visited first, minus the viewer's own."""
        month = month_of(card.period)
        query = (
            select(Venue)
            .join(Visit, Visit.venue_id == Venue.id)
            .where(
                Visit.user_id == card.user_id,
                Visit.status.in_(COUNTED_STATUSES),
                Visit.visit_date.between(month.start, month.end),
                Venue.status == VenueStatus.COUNTER_LIVE,
                Venue.deleted_at.is_(None),
            )
            .group_by(Venue.id)
            .order_by(func.count().desc(), Venue.name)
            .limit(SHARED_PLACES)
        )
        if viewer is not None:
            mine = select(Visit.venue_id).where(
                Visit.user_id == viewer.id, Visit.status.in_(COUNTED_STATUSES)
            )
            query = query.where(Venue.id.not_in(mine))
        venues = list(await self.session.scalars(query))
        places = await Places.load(self.session, venues)
        return [places.brief(v) for v in venues]

    async def _own(self, user: User, code: str) -> NotchCard:
        card = await self.session.scalar(
            select(NotchCard).where(
                NotchCard.card_code == code.strip().upper(), NotchCard.user_id == user.id
            )
        )
        if card is None:
            raise CardNotFoundException()
        return card

    async def _city_name(self, card: NotchCard) -> str:
        return await self.session.scalar(select(City.name).where(City.id == card.city_id)) or ""
