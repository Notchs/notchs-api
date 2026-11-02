"""How a customer is shown to other people. Never their phone number.

Public customers appear as first name + last initial ("Riya S."); private ones (the default)
as "Hidden explorer" (Page Inventory C7).
"""

HIDDEN_NAME = "Hidden explorer"


def short_name(name: str | None) -> str | None:
    """`Riya Shah` -> `Riya S.`, `Riya` -> `Riya`."""
    if not name or not name.strip():
        return None
    words = name.split()
    if len(words) == 1:
        return words[0]
    return f"{words[0]} {words[-1][0].upper()}."


def public_name(name: str | None, is_public: bool) -> str:
    shown = short_name(name) if is_public else None
    return shown or HIDDEN_NAME


def first_name(name: str | None) -> str | None:
    return name.split()[0] if name and name.strip() else None
