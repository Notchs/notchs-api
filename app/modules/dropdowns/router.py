"""`GET /dropdowns/{name}` and `GET /dropdowns?names=a,b`: every form's option lists."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

import app.modules.dropdowns.lists  # noqa: F401  (registers every module's lists)
from app.common.dependencies import DBSession
from app.common.schemas import CustomResponse
from app.modules.dropdowns import registry
from app.modules.dropdowns.exceptions import DropdownFilterInvalidException
from app.modules.dropdowns.schemas import DropdownOption

router = APIRouter(prefix="/dropdowns", tags=["Dropdowns"])

# Lists change rarely (a new city, a new tag), so browsers and the proxy may reuse them briefly.
CACHE_CONTROL = "public, max-age=300"
MAX_NAMES_PER_CALL = 10


def _filters(request: Request, *, skip: str = "") -> dict[str, str]:
    return {key: value for key, value in request.query_params.items() if key != skip}


@router.get(
    "",
    response_model=CustomResponse[dict[str, list[DropdownOption]]],
    summary="Several dropdowns in one call, e.g. ?names=cities,venue_categories",
)
async def many(
    request: Request,
    response: Response,
    session: DBSession,
    names: Annotated[str, Query(description="Comma-separated dropdown names")],
) -> CustomResponse[dict[str, list[DropdownOption]]]:
    wanted = list(dict.fromkeys(n.strip() for n in names.split(",") if n.strip()))
    if not wanted or len(wanted) > MAX_NAMES_PER_CALL:
        raise DropdownFilterInvalidException(
            f"Ask for 1 to {MAX_NAMES_PER_CALL} dropdowns, comma-separated."
        )
    filters = _filters(request, skip="names")
    data = {name: await registry.load(session, name, filters) for name in wanted}
    response.headers["Cache-Control"] = CACHE_CONTROL
    return CustomResponse(message="OK", data=data)


@router.get(
    "/{name}",
    response_model=CustomResponse[list[DropdownOption]],
    summary="One dropdown's options, e.g. /dropdowns/areas?city_id=...",
)
async def one(
    name: str, request: Request, response: Response, session: DBSession
) -> CustomResponse[list[DropdownOption]]:
    data = await registry.load(session, name, _filters(request))
    response.headers["Cache-Control"] = CACHE_CONTROL
    return CustomResponse(message="OK", data=data)
