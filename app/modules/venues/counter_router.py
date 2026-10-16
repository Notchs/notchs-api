"""Counter phone endpoints: the owner's `/venue/counter` and the phone's `/display/*`."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip
from app.common.schemas import CustomResponse
from app.modules.auth.cookies import DEVICE_COOKIE, set_device_cookie
from app.modules.auth.dependencies import CurrentVenueMember
from app.modules.auth.schemas import ConsumeLinkIn
from app.modules.venues.counter_schemas import (
    CounterOut,
    CounterRenameIn,
    DisplayConfigOut,
    PairingLinkOut,
)
from app.modules.venues.counters import CounterService, counter_out
from app.modules.venues.exceptions import DeviceNotPairedException
from app.modules.venues.models import CounterDevice, Venue

owner_router = APIRouter(prefix="/venue/counter", tags=["Venue counter phone"])
display_router = APIRouter(prefix="/display", tags=["Counter phone display"])

# The display config carries the TOTP secret: never let a browser or proxy store it.
NO_STORE = "no-store"


def get_counter_service(session: DBSession, redis: RedisClient) -> CounterService:
    return CounterService(session, redis)


CounterServiceDep = Annotated[CounterService, Depends(get_counter_service)]


async def current_counter(
    request: Request, service: CounterServiceDep
) -> tuple[CounterDevice, Venue]:
    """The paired counter phone calling, from its `nx_device` cookie."""
    raw = request.cookies.get(DEVICE_COOKIE)
    found = await service.device(raw) if raw else None
    if found is None:
        raise DeviceNotPairedException()
    return found


CurrentCounter = Annotated[tuple[CounterDevice, Venue], Depends(current_counter)]


# --- owner ----------------------------------------------------------------------------------


@owner_router.get("", response_model=CustomResponse[CounterOut], summary="The counter phone")
async def get_counter(
    member: CurrentVenueMember, service: CounterServiceDep
) -> CustomResponse[CounterOut]:
    return CustomResponse(message="OK", data=counter_out(await service.get(member)))


@owner_router.post(
    "/pairing-link",
    status_code=status.HTTP_201_CREATED,
    response_model=CustomResponse[PairingLinkOut],
    summary="A 10-minute link to pair (or re-pair) the counter phone",
)
async def pairing_link(
    member: CurrentVenueMember, request: Request, service: CounterServiceDep
) -> CustomResponse[PairingLinkOut]:
    link = await service.pairing_link(member, ip=client_ip(request))
    return CustomResponse(message="Open this link on the counter phone.", data=link)


@owner_router.patch("", response_model=CustomResponse[CounterOut], summary="Rename the counter")
async def rename(
    body: CounterRenameIn, member: CurrentVenueMember, service: CounterServiceDep
) -> CustomResponse[CounterOut]:
    return CustomResponse(
        message="Saved.", data=counter_out(await service.rename(member, body.label))
    )


@owner_router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Remove the counter phone")
async def unpair(member: CurrentVenueMember, service: CounterServiceDep) -> Response:
    await service.unpair(member)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- counter phone ----------------------------------------------------------------------------


@display_router.post(
    "/pair",
    response_model=CustomResponse[DisplayConfigOut],
    summary="Pair this phone with the pairing link's token; sets the device cookie",
)
async def pair(
    body: ConsumeLinkIn, response: Response, service: CounterServiceDep
) -> CustomResponse[DisplayConfigOut]:
    paired = await service.pair(body.token)
    set_device_cookie(response, paired.device_token)
    response.headers["Cache-Control"] = NO_STORE
    return CustomResponse(
        message="This phone is now the counter display.",
        data=service.display_config(paired.counter, paired.venue),
    )


@display_router.get(
    "/config",
    response_model=CustomResponse[DisplayConfigOut],
    summary="What the display needs to show the rotating code",
)
async def config(
    counter: CurrentCounter, request: Request, response: Response, service: CounterServiceDep
) -> CustomResponse[DisplayConfigOut]:
    device, venue = counter
    await service.heartbeat(device)
    set_device_cookie(response, request.cookies[DEVICE_COOKIE])  # keep the year rolling
    response.headers["Cache-Control"] = NO_STORE
    return CustomResponse(message="OK", data=service.display_config(device, venue))


@display_router.post(
    "/heartbeat", status_code=status.HTTP_204_NO_CONTENT, summary="Tell the owner the phone is on"
)
async def heartbeat(
    counter: CurrentCounter, request: Request, service: CounterServiceDep
) -> Response:
    await service.heartbeat(counter[0])
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_device_cookie(response, request.cookies[DEVICE_COOKIE])
    return response
