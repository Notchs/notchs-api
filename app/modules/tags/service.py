"""Tags: the admins' list, each venue's choices, and what customers say they tried."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.modules.scans.models import Visit, VisitStatus
from app.modules.tags.exceptions import (
    TagInUseException,
    TagKindLockedException,
    TagNameTakenException,
    TagNotFoundException,
    TagSelectionInvalidException,
)
from app.modules.tags.models import MAX_NAME_LENGTH, Tag, TagKind
from app.modules.tags.repository import TagRepository
from app.modules.tags.schemas import TagCreate, TagUpdate
from app.modules.users.models import User
from app.modules.venues.slug import slugify

ANSWER_WINDOW = timedelta(hours=24)


class TriedOutcome(StrEnum):
    SAVED = "saved"
    ALREADY_SAVED = "already_saved"
    VISIT_CLOSED = "visit_closed"  # too late, or not this customer's visit
    NOT_ON_MENU = "not_on_menu"  # the venue has since removed it


@dataclass(frozen=True, slots=True)
class TriedResult:
    outcome: TriedOutcome
    tag: Tag | None = None


class TagService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = TagRepository(session)

    # --- admins -----------------------------------------------------------------------

    async def list(
        self, params: PaginationParams, *, kind: TagKind | None
    ) -> tuple[Sequence[Tag], int]:
        return await self.repository.list_by_kind(kind, offset=params.offset, limit=params.size)

    async def get(self, tag_id: uuid.UUID) -> Tag:
        tag = await self.repository.get(tag_id)
        if tag is None:
            raise TagNotFoundException()
        return tag

    async def create(self, body: TagCreate) -> Tag:
        tag = Tag(name=body.name, slug=_slug(body.name), kind=body.kind)
        try:
            async with self.session.begin_nested():
                self.session.add(tag)
                await self.session.flush()
        except IntegrityError as exc:
            raise TagNameTakenException() from exc
        await self.session.refresh(tag)
        return tag

    async def update(self, tag_id: uuid.UUID, body: TagUpdate) -> Tag:
        tag = await self.get(tag_id)
        if body.kind is not None and body.kind != tag.kind:
            if await self.repository.is_used(tag.id):
                raise TagKindLockedException()
            tag.kind = body.kind
        if body.name is not None:
            tag.name, tag.slug = body.name, _slug(body.name)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise TagNameTakenException() from exc
        await self.session.refresh(tag)
        return tag

    async def delete(self, tag_id: uuid.UUID) -> None:
        """Also takes it off venues' lists. Refused once a customer has picked it."""
        tag = await self.get(tag_id)
        if await self.repository.is_answered(tag.id):
            raise TagInUseException()
        try:
            async with self.session.begin_nested():
                await self.repository.delete(tag)
        except IntegrityError as exc:  # picked in the moment since the check
            raise TagInUseException() from exc

    # --- venues -----------------------------------------------------------------------

    async def set_venue_tags(self, venue_id: uuid.UUID, tag_ids: list[uuid.UUID]) -> Sequence[Tag]:
        await self.repository.lock_venue(venue_id)
        tags = await self.repository.by_ids(tag_ids)
        if len(tags) != len(tag_ids) or any(tag.kind == TagKind.ITEM for tag in tags):
            raise TagSelectionInvalidException()
        await self.repository.replace_venue_tags(venue_id, tag_ids)
        return await self.repository.venue_tags(venue_id)

    async def set_venue_items(self, venue_id: uuid.UUID, tag_ids: list[uuid.UUID]) -> Sequence[Tag]:
        await self.repository.lock_venue(venue_id)
        tags = await self.repository.by_ids(tag_ids)
        if len(tags) != len(tag_ids) or any(tag.kind != TagKind.ITEM for tag in tags):
            raise TagSelectionInvalidException()
        await self.repository.replace_venue_items(venue_id, tag_ids)
        return await self.repository.venue_items(venue_id)

    # --- customers --------------------------------------------------------------------

    async def record_tried(
        self, user: User, visit_id: uuid.UUID, tag_id: uuid.UUID, *, now: datetime | None = None
    ) -> TriedResult:
        """A tap on the "What did you try?" menu. Earns no points."""
        now = now or datetime.now(UTC)
        visit = await self.session.get(Visit, visit_id)
        if (
            visit is None
            or visit.user_id != user.id
            or visit.status != VisitStatus.VALID
            or now - visit.visited_at > ANSWER_WINDOW
            or user.consent_at is None  # nothing is collected before they agree
        ):
            return TriedResult(TriedOutcome.VISIT_CLOSED)
        tag = await self.repository.venue_item(visit.venue_id, tag_id)
        if tag is None:
            return TriedResult(TriedOutcome.NOT_ON_MENU)
        saved = await self.repository.add_visit_tag(visit.id, tag.id)
        return TriedResult(TriedOutcome.SAVED if saved else TriedOutcome.ALREADY_SAVED, tag)


def _slug(name: str) -> str:
    return slugify(name, max_length=MAX_NAME_LENGTH)
