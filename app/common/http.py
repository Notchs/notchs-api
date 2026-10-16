"""Small helpers for reading request details."""

import ipaddress

from starlette.requests import Request

MAX_USER_AGENT_LENGTH = 255  # auth_tokens.user_agent is VARCHAR(255)


def client_ip(request: Request) -> str | None:
    """The caller's IP address, or None if it is missing or not a valid address.

    Behind a proxy this relies on uvicorn's `--proxy-headers` (the prod image sets it).
    """
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:MAX_USER_AGENT_LENGTH] if value else None
