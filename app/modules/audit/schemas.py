import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BeforeValidator

from app.common.schemas import BaseSchema
from app.modules.audit.models import ActorType

# The database driver returns INET values as ipaddress objects.
IpAddress = Annotated[str | None, BeforeValidator(lambda v: None if v is None else str(v))]


class AuditLogOut(BaseSchema):
    id: int
    created_at: datetime
    actor_type: ActorType
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip_address: IpAddress
    request_id: str | None
