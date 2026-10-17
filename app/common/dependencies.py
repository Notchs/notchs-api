"""Reusable FastAPI dependencies. Use the `Annotated` aliases in route signatures."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.session import get_db_session

DBSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]


@dataclass(frozen=True, slots=True)
class PaginationParams:
    page: int = Query(1, ge=1, description="1-based page number")
    size: int = Query(20, ge=1, le=100, description="Items per page")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


Pagination = Annotated[PaginationParams, Depends()]
