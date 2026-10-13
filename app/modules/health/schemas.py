from typing import Literal

from app.common.schemas import BaseSchema

Status = Literal["ok", "error"]


class HealthResponse(BaseSchema):
    status: Status
    app: str
    version: str
    environment: str


class ReadinessResponse(BaseSchema):
    status: Status
    database: Status
    redis: Status
