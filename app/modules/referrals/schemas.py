"""The customer's invite card. Inviters and invitees appear by handle, never by number."""

from pydantic import Field

from app.common.schemas import BaseSchema


class ReferralOut(BaseSchema):
    # Their handle: what friends type, and the `?ref=` in the link.
    code: str
    link: str
    star_points: int
    invited: int
    # Invited friends who got their first Notch (each one is worth 50 Star Points).
    rewarded: int
    invited_by: str | None
    # True while they can still add who invited them.
    can_add_inviter: bool


class InviterIn(BaseSchema):
    code: str = Field(min_length=3, max_length=31)
