"""`/dev/login-as-customer`: log in as a test customer from the API docs. Never in production.

Customers have no password: they log in with the single-use link the WhatsApp bot sends. This
runs that same link path (same checks, same limit of 3 links per 15 minutes) without WhatsApp,
and sets the customer's login cookies like `POST /auth/customer/consume`. Admins (role `admin`)
only, like the bot simulator. It never creates an account: the number must already belong to
an active customer who has agreed to the privacy notice.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.common.dependencies import DBSession, RedisClient, not_in_production
from app.common.http import client_ip, user_agent
from app.common.schemas import CustomResponse
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.auth.cookies import set_session_cookies
from app.modules.auth.customer_auth import CustomerAuthService
from app.modules.auth.dependencies import CurrentFullAdmin
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import DevCustomerNotFoundException
from app.modules.auth.schemas import CustomerBriefOut, DevCustomerLoginIn, SessionOut
from app.modules.auth.sessions import active_user
from app.modules.users.models import User

logger = get_logger(__name__)

router = APIRouter(
    prefix="/dev",
    tags=["Dev tools"],
    dependencies=[Depends(not_in_production)],
    include_in_schema=not settings.is_production,
)


def get_customer_auth_service(session: DBSession, redis: RedisClient) -> CustomerAuthService:
    return CustomerAuthService(session, redis)


@router.post(
    "/login-as-customer",
    response_model=CustomResponse[CustomerBriefOut],
    summary="Log in as a test customer by their number, without WhatsApp (not in production)",
)
async def login_as_customer(
    body: DevCustomerLoginIn,
    request: Request,
    response: Response,
    admin: CurrentFullAdmin,
    session: DBSession,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
) -> CustomResponse[CustomerBriefOut]:
    user = await session.scalar(select(User).where(User.phone_e164 == body.phone))
    if user is None or user.consent_at is None or await active_user(session, user.id) is None:
        raise DevCustomerNotFoundException()

    login = await auth.dev_login(user, ip=client_ip(request), user_agent=user_agent(request))
    issued = login.session
    set_session_cookies(
        response,
        SubjectType.USER,
        access_token=issued.access_token,
        access_expires_at=issued.access_expires_at,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )
    # IDs only, never the number.
    logger.info("Admin %s logged in as customer %s (dev tool)", admin.id, user.id)
    return CustomResponse(
        message="Logged in as this customer.",
        data=CustomerBriefOut(
            id=user.id,
            name=user.name,
            handle=user.handle,
            is_public=user.is_public,
            session=SessionOut(access_expires_at=issued.access_expires_at),
        ),
    )
