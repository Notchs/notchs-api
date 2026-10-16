"""Generic service layer on top of `BaseRepository`.

Module services subclass this and add business rules; standard CRUD comes for free.
"""

import uuid
from collections.abc import Sequence

from pydantic import BaseModel

from app.common.dependencies import PaginationParams
from app.common.exceptions import NotFoundException
from app.common.repository import BaseRepository
from app.db.base import Base


class BaseService[ModelT: Base]:
    not_found_message: str = "Resource not found."

    def __init__(self, repository: BaseRepository[ModelT]) -> None:
        self.repository = repository

    async def get_by_id(self, id_: uuid.UUID) -> ModelT:
        obj = await self.repository.get(id_)
        if obj is None:
            raise NotFoundException(self.not_found_message)
        return obj

    async def list_paginated(self, params: PaginationParams) -> tuple[Sequence[ModelT], int]:
        items = await self.repository.list(offset=params.offset, limit=params.size)
        return items, await self.repository.count()

    async def create(self, payload: BaseModel) -> ModelT:
        return await self.repository.create(payload.model_dump())

    async def update(self, id_: uuid.UUID, payload: BaseModel) -> ModelT:
        obj = await self.get_by_id(id_)
        return await self.repository.update(obj, payload.model_dump(exclude_unset=True))

    async def delete(self, id_: uuid.UUID) -> None:
        await self.repository.delete(await self.get_by_id(id_))
