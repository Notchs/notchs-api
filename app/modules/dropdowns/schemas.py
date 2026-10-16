from app.common.schemas import BaseSchema


class DropdownOption(BaseSchema):
    """One choice in a dropdown: `value` is sent back by the form, `label` is shown."""

    value: str
    label: str
