"""The customer's passport on the website: who they are, their Notchs and the monthly reward."""

from datetime import UTC, date, datetime

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.storage import PhotoStorage, StorageError
from app.common.schemas import PaginatedResponse
from app.common.time import ist_date
from app.core import rate_limit
from app.core.logging import get_logger
from app.modules.consents.models import Consent, ConsentChannel, ConsentPurpose
from app.modules.consents.service import record_user_consent
from app.modules.locations.models import Area, City
from app.modules.passport import rewards
from app.modules.passport.exceptions import (
    PassportAreaInvalidException,
    PassportBirthDateInvalidException,
    PhotoInvalidException,
    PhotoRateLimitedException,
    PhotoStorageFailedException,
    PhotoStorageNotConfiguredException,
)
from app.modules.passport.repository import PassportRepository
from app.modules.passport.schemas import (
    AgeOut,
    AreaNotchsOut,
    CategoryPlacesOut,
    CityNotchsOut,
    MeOut,
    MeUpdate,
    NotchOut,
    PlaceRef,
    RewardOut,
    Totals,
    VisitedPlaceOut,
)
from app.modules.scans.niches import niche_of
from app.modules.users.age import MIN_AGE, age_on, age_range
from app.modules.users.consent import BIRTHDAY_CONSENT_TEXT, BIRTHDAY_CONSENT_VERSION
from app.modules.users.models import User
from app.modules.users.photos import CONTENT_TYPE, PhotoInvalidError, clean_photo, photo_key

logger = get_logger(__name__)

MAX_PHOTOS_PER_HOUR = 10
OLDEST_BIRTH_DATE = date(1900, 1, 1)


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


class PassportService:
    def __init__(
        self,
        session: AsyncSession,
        storage: PhotoStorage | None = None,
        redis: Redis | None = None,
    ) -> None:
        self.session = session
        self.repository = PassportRepository(session)
        self.storage = storage
        self.redis = redis

    async def me(self, user: User, now: datetime | None = None) -> MeOut:
        today = ist_date(now or datetime.now(UTC))
        city = await self.session.get(City, user.city_id) if user.city_id else None
        area = await self.session.get(Area, user.area_id) if user.area_id else None
        notches, places, points = await self.repository.totals(user.id)
        return MeOut(
            name=user.name,
            handle=user.handle,
            phone=user.phone_e164,
            photo_url=await self._photo_url(user),
            city=PlaceRef(id=city.id, name=city.name) if city else None,
            area=PlaceRef(id=area.id, name=area.name) if area else None,
            is_public=user.is_public,
            theme=user.theme,
            founding_member_no=user.founding_member_no,
            instagram_handle=user.instagram_handle,
            show_instagram_to_venues=user.show_instagram_to_venues,
            member_since=user.created_at,
            age=AgeOut(
                birth_date=user.birth_date,
                age_range=age_range(user.birth_date, today),
                adult_confirmed_at=user.age_confirmed_at,
                consent_text=BIRTHDAY_CONSENT_TEXT,
                consent_version=BIRTHDAY_CONSENT_VERSION,
            ),
            totals=Totals(notches=notches, places=places, points=points),
        )

    async def update_me(self, user: User, body: MeUpdate) -> MeOut:
        changes = body.model_dump(exclude_unset=True)
        if "area_id" in changes:
            area_id = changes.pop("area_id")
            if area_id is None:
                user.area_id = None
            else:
                # Only an area of the customer's city (any city before their first scan).
                area = await self.repository.area_in_city(area_id, user.city_id)
                if area is None:
                    raise PassportAreaInvalidException()
                user.area_id = area.id
                user.city_id = area.city_id
        for field in ("name", "theme", "show_instagram_to_venues"):
            if changes.get(field) is not None:
                setattr(user, field, changes[field])
        if "instagram_handle" in changes:
            user.instagram_handle = changes["instagram_handle"]
            if user.instagram_handle is None:
                user.show_instagram_to_venues = False
        if "birth_date" in changes:
            await self._set_birth_date(user, changes["birth_date"])
        await self.session.flush()
        return await self.me(user)

    async def _set_birth_date(self, user: User, birth_date: date | None) -> None:
        """Saving it for the first time records the consent with its exact words; removing it
        withdraws that consent. Changing an existing date keeps the consent already given."""
        now = datetime.now(UTC)
        if birth_date is None:
            if user.birth_date is not None:
                await self.session.execute(
                    update(Consent)
                    .where(
                        Consent.user_id == user.id,
                        Consent.purpose == ConsentPurpose.BIRTHDAY,
                        Consent.withdrawn_at.is_(None),
                    )
                    .values(withdrawn_at=now)
                )
            user.birth_date = None
            return
        today = ist_date(now)
        if birth_date < OLDEST_BIRTH_DATE or age_on(birth_date, today) < MIN_AGE:
            raise PassportBirthDateInvalidException()
        if user.birth_date is None:
            await record_user_consent(
                self.session,
                user.id,
                purpose=ConsentPurpose.BIRTHDAY,
                version=BIRTHDAY_CONSENT_VERSION,
                wording=BIRTHDAY_CONSENT_TEXT,
                channel=ConsentChannel.WEBSITE,
                accepted_at=now,
            )
        user.birth_date = birth_date

    async def places(self, user: User) -> list[CategoryPlacesOut]:
        """Places visited, by kind; the kind with the most visits first."""
        groups: dict[str, CategoryPlacesOut] = {}
        for row in await self.repository.visited_places(user.id):
            group = groups.get(row.niche)
            if group is None:
                niche = niche_of(row.niche)
                group = groups[row.niche] = CategoryPlacesOut(
                    niche=row.niche,
                    label=niche.label,
                    colour=niche.colour,
                    places=0,
                    visits=0,
                    venues=[],
                )
            group.places += 1
            group.visits += row.visits
            group.venues.append(
                VisitedPlaceOut(
                    venue_code=row.venue_code,
                    venue_name=row.venue_name,
                    area=row.area,
                    city=row.city,
                    visits=row.visits,
                    first_visit=row.first_visit,
                    last_visit=row.last_visit,
                )
            )
        return sorted(groups.values(), key=lambda g: (-g.visits, -g.places, g.label))

    async def notchs_by_location(self, user: User) -> list[CityNotchsOut]:
        """Notchs per city and area, the busiest first."""
        cities: dict[object, CityNotchsOut] = {}
        for row in await self.repository.notchs_by_area(user.id):
            city = cities.get(row.city_id)
            if city is None:
                city = cities[row.city_id] = CityNotchsOut(
                    city=PlaceRef(id=row.city_id, name=row.city_name),
                    notchs=0,
                    places=0,
                    areas=[],
                )
            city.notchs += row.notchs
            city.areas.append(
                AreaNotchsOut(
                    area=PlaceRef(id=row.area_id, name=row.area_name) if row.area_id else None,
                    notchs=row.notchs,
                    places=row.places,
                )
            )
        places = await self.repository.places_per_city(user.id)
        for city_id, city in cities.items():
            city.places = places.get(city_id, 0)
            city.areas.sort(key=lambda a: (-a.notchs, a.area is None, a.area and a.area.name))
        return sorted(cities.values(), key=lambda c: (-c.notchs, c.city.name))

    # --- profile picture --------------------------------------------------------------------

    async def set_photo(self, user: User, data: bytes) -> MeOut:
        storage = self._configured_storage()
        assert self.redis is not None
        if not await rate_limit.hit(
            self.redis, f"rl:photo:{user.id}", limit=MAX_PHOTOS_PER_HOUR, window_seconds=3600
        ):
            raise PhotoRateLimitedException()
        try:
            cleaned = clean_photo(data)
        except PhotoInvalidError as exc:
            raise PhotoInvalidException() from exc
        key = photo_key(user.id)
        try:
            await storage.put(key, cleaned, CONTENT_TYPE)
        except StorageError as exc:
            raise PhotoStorageFailedException() from exc
        old = user.photo_s3_key
        user.photo_s3_key = key
        await self.session.flush()
        if old:
            await self._delete_quietly(old)
        logger.info("Customer %s changed their photo", user.id)
        return await self.me(user)

    async def remove_photo(self, user: User) -> MeOut:
        old = user.photo_s3_key
        user.photo_s3_key = None
        await self.session.flush()
        if old and self.storage is not None and self.storage.configured:
            await self._delete_quietly(old)
        return await self.me(user)

    def _configured_storage(self) -> PhotoStorage:
        if self.storage is None or not self.storage.configured:
            raise PhotoStorageNotConfiguredException()
        return self.storage

    async def _photo_url(self, user: User) -> str | None:
        if not user.photo_s3_key or self.storage is None or not self.storage.configured:
            return None
        try:
            return await self.storage.signed_url(user.photo_s3_key)
        except Exception:  # a broken link must never break the whole profile
            logger.exception("Could not sign the photo link for customer %s", user.id)
            return None

    async def _delete_quietly(self, key: str) -> None:
        """The profile already points away from it; a leftover file is only logged."""
        assert self.storage is not None
        try:
            await self.storage.delete(key)
        except StorageError:
            logger.warning("Old photo %s was not deleted from storage", key)

    async def notches(self, user: User, page: int, size: int) -> PaginatedResponse[NotchOut]:
        rows, total = await self.repository.notches(user.id, offset=(page - 1) * size, limit=size)
        items = []
        for row in rows:
            niche = niche_of(row.category)
            items.append(
                NotchOut(
                    notch_no=row.notch_no,
                    venue_name=row.venue_name,
                    venue_area=row.venue_area,
                    niche=row.category,
                    colour=niche.colour,
                    visit_date=row.visit_date,
                    visited_at=row.visited_at,
                    visit_no_at_venue=row.visit_no_at_venue,
                    points=row.points,
                )
            )
        return PaginatedResponse.build(items, total, page, size)

    async def reward(self, user: User, now: datetime | None = None) -> RewardOut:
        today = ist_date(now or datetime.now(UTC))
        start, end = _month_bounds(today)
        new_cafes = await self.repository.first_visits_between(
            user.id, rewards.REWARD_CATEGORY, start, end
        )
        return RewardOut(
            month=start.strftime("%Y-%m"),
            new_cafes=new_cafes,
            needed=rewards.NEW_CAFES_NEEDED,
            eligible=new_cafes >= rewards.NEW_CAFES_NEEDED,
            value_rupees=rewards.REWARD_VALUE_RUPEES,
        )
