"""Request and response bodies for tags."""

import re
import uuid
from typing import Annotated

from pydantic import AfterValidator, Field, field_validator

from app.common.schemas import BaseSchema, EntitySchema
from app.modules.tags.models import MAX_NAME_LENGTH, TagKind

MAX_VENUE_TAGS = 5
MAX_VENUE_ITEMS = 10


def _has_latin_character(name: str) -> str:
    """The slug is made from these. A name of only symbols would have none."""
    if not re.search(r"[A-Za-z0-9]", name):
        raise ValueError("A tag name needs at least one letter or digit")
    return name


TagName = Annotated[
    str, Field(min_length=1, max_length=MAX_NAME_LENGTH), AfterValidator(_has_latin_character)
]


class TagCreate(BaseSchema):
    name: TagName
    kind: TagKind


class TagUpdate(BaseSchema):
    name: TagName | None = None
    kind: TagKind | None = None


class TagOut(EntitySchema):
    name: str
    slug: str
    kind: str


class _TagSelection(BaseSchema):
    tag_ids: list[uuid.UUID]

    @field_validator("tag_ids")
    @classmethod
    def _no_repeats(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("The same tag was chosen twice")
        return value


class VenueTagsIn(_TagSelection):
    tag_ids: list[uuid.UUID] = Field(max_length=MAX_VENUE_TAGS)


class VenueItemsIn(_TagSelection):
    """In the order the bot's menu should show them."""

    tag_ids: list[uuid.UUID] = Field(max_length=MAX_VENUE_ITEMS)
