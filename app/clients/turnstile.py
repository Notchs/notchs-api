"""Cloudflare Turnstile, the CAPTCHA on public forms.

https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
"""

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 10.0
MAX_TOKEN_LENGTH = 2048  # Cloudflare's documented maximum


class TurnstileClient:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    async def verify(self, token: str, *, ip: str | None = None) -> bool:
        """True if Cloudflare accepts the token. Fails closed: errors count as a failed check."""
        if settings.TURNSTILE_DISABLED:
            return True
        if not token or len(token) > MAX_TOKEN_LENGTH:
            return False
        if settings.TURNSTILE_SECRET is None:
            logger.error("TURNSTILE_SECRET is not set; refusing every CAPTCHA check")
            return False

        data = {"secret": settings.TURNSTILE_SECRET.get_secret_value(), "response": token}
        if ip:
            data["remoteip"] = ip
        try:
            if self._http is not None:
                response = await self._http.post(SITEVERIFY_URL, data=data)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
                    response = await http.post(SITEVERIFY_URL, data=data)
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPError, ValueError:
            logger.exception("Turnstile check could not be completed")
            return False

        if not result.get("success"):
            logger.info("Turnstile rejected a token: %s", result.get("error-codes"))
            return False
        return True


def get_turnstile_client() -> TurnstileClient:
    """FastAPI dependency. Override it in tests."""
    return TurnstileClient()
