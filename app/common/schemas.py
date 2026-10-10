"""Shared Pydantic schemas: base config, timestamps, response envelopes, pagination."""

import math
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
)

# Human-readable public code, e.g. "VEN-7K3M9QX2": 3-letter table prefix + 8 Crockford
# Base32 chars (no I, L, O, U). The UUID `id` stays the primary key; declare the code per
# module as `<entity>_code: PublicCode`. See docs/WORKPLAN.md §1.1.
PublicCode = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[A-Z]{3}-[0-9A-HJKMNP-TV-Z]{8}$"),
]


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, populate_by_name=True, str_strip_whitespace=True
    )


class TimestampSchema(BaseSchema):
    """Timestamp + audit fields; reuse for any read schema that tracks them.

    `*_by` fields hold the acting user's id. Audit and soft-delete fields are optional
    because `app.db.base.TimestampMixin` does not define those columns yet.
    """

    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
    deleted_at: datetime | None = None
    deleted_by: uuid.UUID | None = None


class EntitySchema(TimestampSchema):
    """Base for read schemas of models built on `app.db.base.Entity`.

    `id` is the UUID primary key. Tables with a public code add `<entity>_code: PublicCode`.
    """

    id: uuid.UUID


class CustomResponse[T](BaseSchema):
    """Success envelope: `{"success": true, "message": ..., "data": ...}`.

    `data` is omitted from the output when it is None.
    """

    success: Literal[True] = True
    message: str
    data: T | None = None

    @model_serializer(mode="wrap")
    def _drop_empty_data(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if result.get("data") is None:
            result.pop("data", None)
        return result


class PaginatedResponse[T](BaseSchema):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, size: int) -> PaginatedResponse[T]:
        return cls(items=items, total=total, page=page, size=size, pages=math.ceil(total / size))


class ErrorBody(BaseSchema):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseSchema):
    """Error envelope: `{"success": false, "error": {...}}`."""

    success: Literal[False] = False
    error: ErrorBody
