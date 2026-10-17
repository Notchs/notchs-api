"""Phone numbers in E.164 form (`+919876543210`), the format every phone column stores."""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

E164 = re.compile(r"\+[1-9][0-9]{7,14}")
_INDIAN_MOBILE = re.compile(r"[6-9][0-9]{9}")
_SEPARATORS = re.compile(r"[\s\-().]")


def normalize_phone(value: str) -> str:
    """`98765 43210`, `+91 98765-43210`, `0919876543210` -> `+919876543210`.

    A bare 10-digit number is read as an Indian mobile. Anything else must already be in
    international `+` form. Raises ValueError for anything that is not a phone number.
    """
    digits = _SEPARATORS.sub("", value.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if _INDIAN_MOBILE.fullmatch(digits):
        return "+91" + digits
    if digits.startswith("0") and _INDIAN_MOBILE.fullmatch(digits[1:]):
        return "+91" + digits[1:]
    if digits.startswith("91") and _INDIAN_MOBILE.fullmatch(digits[2:]):
        return "+" + digits
    if E164.fullmatch(digits):
        if digits.startswith("+91") and not _INDIAN_MOBILE.fullmatch(digits[3:]):
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return digits
    raise ValueError("Enter a valid phone number, e.g. 98765 43210 or +91 98765 43210")


def masked_phone(phone: str) -> str:
    """For logs: only the last 4 digits, e.g. `…3210`."""
    return f"…{phone[-4:]}"


# A request field: any common way of typing a number, stored and returned as E.164.
Phone = Annotated[str, Field(max_length=32), AfterValidator(normalize_phone)]
