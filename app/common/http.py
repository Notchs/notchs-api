"""Small helpers for reading request details."""

import hmac
import ipaddress
from dataclasses import dataclass

from starlette.requests import Request

from app.core.config import settings

MAX_USER_AGENT_LENGTH = 255  # auth_tokens.user_agent is VARCHAR(255)

# Sent by the Notchs website with every API call it proxies (notchs-web, src/proxy.ts).
PROXY_SECRET_HEADER = "x-notchs-proxy-secret"
PROXY_CLIENT_IP_HEADER = "x-notchs-client-ip"
# Set by the platforms in front of the API, each to the address that connected to *them*:
# Netlify (the website) and Render's edge (Cloudflare).
NETLIFY_CLIENT_IP = "x-nf-client-connection-ip"
EDGE_CLIENT_IP = ("cf-connecting-ip", "true-client-ip")


@dataclass(frozen=True, slots=True)
class ClientAddress:
    ip: str | None
    # Where it came from: "website", "netlify", "cf-connecting-ip", "true-client-ip",
    # "forwarded", "socket" or "unknown".
    source: str


def _address(value: str | None) -> str | None:
    """`value` as a valid IP address, or None."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _public(value: str | None) -> str | None:
    address = _address(value)
    return address if address and ipaddress.ip_address(address).is_global else None


def from_website(request: Request) -> bool:
    """True when the request carries the secret only the Notchs website knows."""
    secret = settings.PROXY_SECRET
    given = request.headers.get(PROXY_SECRET_HEADER)
    if secret is None or not given:
        return False
    return hmac.compare_digest(given.encode(), secret.get_secret_value().encode())


def client_address(request: Request) -> ClientAddress:
    """The visitor's IP address, for rate limits, and how it was found.

    The API sits behind proxies, so the socket's address is a load balancer's. In order:

    1. The website's word, when it proves itself with PROXY_SECRET: the address Netlify saw.
       Nobody else can send this, so it can't be made up.
    2. Without PROXY_SECRET configured: Netlify's own header (a direct caller could invent it).
    3. The edge's header: who connected to Render.
    4. The last public address in X-Forwarded-For (each proxy appends the address it saw; the
       first entries can be made up by the caller and are never used).
    5. The socket's own address (local development).

    Limits that guard an account (logins, codes) are also counted per account, never per IP only.
    """
    headers = request.headers
    if from_website(request):
        address = _address(headers.get(PROXY_CLIENT_IP_HEADER))
        if address:
            return ClientAddress(address, "website")
    elif settings.PROXY_SECRET is None:
        address = _public(headers.get(NETLIFY_CLIENT_IP))
        if address:
            return ClientAddress(address, "netlify")
    for name in EDGE_CLIENT_IP:
        address = _public(headers.get(name))
        if address:
            return ClientAddress(address, name)
    for part in reversed(headers.get("x-forwarded-for", "").split(",")):
        address = _public(part)
        if address:
            return ClientAddress(address, "forwarded")
    address = _address(request.client.host if request.client else None)
    return ClientAddress(address, "socket" if address else "unknown")


def client_ip(request: Request) -> str | None:
    """The visitor's IP address (see `client_address`). None if it can't be told."""
    return client_address(request).ip


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:MAX_USER_AGENT_LENGTH] if value else None
