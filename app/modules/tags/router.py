"""`/admin/tags`: only admins change the list. `/venue/tags` and `/venue/items`: a venue picks
from it. Viewing is open to reviewers; changes need the role `admin`."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.common.dependencies import DBSession, Pagination
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.auth.dependencies import CurrentAdmin, CurrentFullAdmin, CurrentVenueMember
from app.modules.tags.models import TagKind
from app.modules.tags.repository import TagRepository
from app.modules.tags.schemas import TagCreate, TagOut, TagUpdate, VenueItemsIn, VenueTagsIn
from app.modules.tags.service import TagService

admin_router = APIRouter(prefix="/admin/tags", tags=["Admin: tags"])
venue_router = APIRouter(prefix="/venue", tags=["Venue tags"])


def get_tag_service(session: DBSession) -> TagService:
    return TagService(session)


TagServiceDep = Annotated[TagService, Depends(get_tag_service)]


def _out(tags) -> list[TagOut]:
    return [TagOut.model_validate(tag) for tag in tags]


# --- admins -----------------------------------------------------------------------------------


@admin_router.get(
    "", response_model=CustomResponse[PaginatedResponse[TagOut]], summary="All tags, by name"
)
async def list_tags(
    _: CurrentAdmin, service: TagServiceDep, pagination: Pagination, kind: TagKind | None = None
) -> CustomResponse[PaginatedResponse[TagOut]]:
    items, total = await service.list(pagination, kind=kind)
    page = PaginatedResponse.build(_out(items), total, pagination.page, pagination.size)
    return CustomResponse(message="OK", data=page)


@admin_router.get("/{tag_id}", response_model=CustomResponse[TagOut], summary="One tag")
async def get_tag(
    tag_id: uuid.UUID, _: CurrentAdmin, service: TagServiceDep
) -> CustomResponse[TagOut]:
    return CustomResponse(message="OK", data=TagOut.model_validate(await service.get(tag_id)))


@admin_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CustomResponse[TagOut],
    summary="Add a tag",
)
async def create_tag(
    body: TagCreate, _: CurrentFullAdmin, service: TagServiceDep
) -> CustomResponse[TagOut]:
    return CustomResponse(message="Added.", data=TagOut.model_validate(await service.create(body)))


@admin_router.patch(
    "/{tag_id}",
    response_model=CustomResponse[TagOut],
    summary="Rename a tag, or change its kind while nothing uses it",
)
async def update_tag(
    tag_id: uuid.UUID, body: TagUpdate, _: CurrentFullAdmin, service: TagServiceDep
) -> CustomResponse[TagOut]:
    tag = await service.update(tag_id, body)
    return CustomResponse(message="Saved.", data=TagOut.model_validate(tag))


@admin_router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tag, unless customers have picked it",
)
async def delete_tag(tag_id: uuid.UUID, _: CurrentFullAdmin, service: TagServiceDep) -> Response:
    await service.delete(tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- venues -----------------------------------------------------------------------------------


@venue_router.get("/tags", response_model=CustomResponse[list[TagOut]], summary="What the place is")
async def get_venue_tags(
    member: CurrentVenueMember, session: DBSession
) -> CustomResponse[list[TagOut]]:
    tags = await TagRepository(session).venue_tags(member.venue_id)
    return CustomResponse(message="OK", data=_out(tags))


@venue_router.put(
    "/tags", response_model=CustomResponse[list[TagOut]], summary="Replace the venue's tags (max 5)"
)
async def set_venue_tags(
    body: VenueTagsIn, member: CurrentVenueMember, service: TagServiceDep
) -> CustomResponse[list[TagOut]]:
    tags = await service.set_venue_tags(member.venue_id, body.tag_ids)
    return CustomResponse(message="Saved.", data=_out(tags))


@venue_router.get(
    "/items", response_model=CustomResponse[list[TagOut]], summary="The 'What did you try?' menu"
)
async def get_venue_items(
    member: CurrentVenueMember, session: DBSession
) -> CustomResponse[list[TagOut]]:
    items = await TagRepository(session).venue_items(member.venue_id)
    return CustomResponse(message="OK", data=_out(items))


@venue_router.put(
    "/items",
    response_model=CustomResponse[list[TagOut]],
    summary="Replace the menu, in order (max 10)",
)
async def set_venue_items(
    body: VenueItemsIn, member: CurrentVenueMember, service: TagServiceDep
) -> CustomResponse[list[TagOut]]:
    items = await service.set_venue_items(member.venue_id, body.tag_ids)
    return CustomResponse(message="Saved.", data=_out(items))
