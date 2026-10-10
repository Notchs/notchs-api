"""Identifier helpers: UUIDv7 primary keys and human-readable public codes.

See docs/WORKPLAN.md §1.1 for the rules and the prefix list.
"""

import re
import secrets
import uuid

# Crockford Base32: no I, L, O, U, so codes survive being read aloud or retyped.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
PREFIX_PATTERN = re.compile(r"[A-Z]{3}")


def new_uuid() -> uuid.UUID:
    """Time-ordered UUIDv7: inserts land at the end of the PK index, not at random."""
    return uuid.uuid7()


def new_code(prefix: str) -> str:
    """Random public code such as `VEN-7K3M9QX2` (32^8 ≈ 1.1 trillion per prefix)."""
    if not PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError(f"Code prefix must be 3 uppercase letters, got {prefix!r}")
    body = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{prefix}-{body}"
