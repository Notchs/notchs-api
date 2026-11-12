"""Small helpers for reading request details."""

import ipaddress

from starlette.requests import Request

MAX_USER_AGENT_LENGTH = 255  # auth_tokens.user_agent is VARCHAR(255)

# Set by the platforms in front of the API, each to the address that connected to *them*:
# Netlify (the website proxies /api/v1 through it) and Render's edge (Cloudflare).
NETLIFY_CLIENT_IP = "x-nf-client-connection-ip"
EDGE_CLIENT_IP = ("cf-connecting-ip", "true-client-ip")


def _address(value: str | None) -> str | None:
    """`value` as a valid IP address, or None."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _is_public(address: str) -> bool:
    return ipaddress.ip_address(address).is_global


def client_ip(request: Request) -> str | None:
    """The visitor's IP address, for rate limits. None if it can't be told.

    The API sits behind proxies, so the socket's address is a load balancer's (a different one
    on each request, which made every per-IP limit useless). In order:

    1. Netlify's header: the visitor's address when the request came through the website.
    2. The edge's header: who connected to Render (the visitor, for direct calls).
    3. The last public address in X-Forwarded-For (each proxy appends the address it saw, so
       the first entries can be made up by the caller and are never used).
    4. The socket's own address (local development).

    Someone calling the API directly could invent header 1 to get a fresh per-IP allowance, so
    limits that guard an account (logins, codes) are also counted per account, never per IP only.
    """
    headers = request.headers
    for name in (NETLIFY_CLIENT_IP, *EDGE_CLIENT_IP):
        address = _address(headers.get(name))
        if address and _is_public(address):
            return address
    forwarded = [_address(part) for part in headers.get("x-forwarded-for", "").split(",")]
    for address in reversed(forwarded):
        if address and _is_public(address):
            return address
    return _address(request.client.host if request.client else None)


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:MAX_USER_AGENT_LENGTH] if value else None
