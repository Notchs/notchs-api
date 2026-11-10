"""The tag schemas, the WhatsApp list message and the menu's row ids, with no database."""

import uuid

import pytest
from pydantic import ValidationError

from app.modules.tags.models import Tag
from app.modules.tags.schemas import (
    MAX_VENUE_ITEMS,
    MAX_VENUE_TAGS,
    TagCreate,
    VenueItemsIn,
    VenueTagsIn,
)
from app.modules.whatsapp import payloads
from app.modules.whatsapp.bot import parse_try_row, try_row_id, try_rows
from app.modules.whatsapp.dev_router import _text_of
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.payloads import ListRow

PHONE = "+919876543210"


def tap(button_id: str, kind: str = "button") -> WhatsAppMessage:
    return WhatsAppMessage(
        direction="in",
        phone_e164=PHONE,
        message_type=kind,
        body="x",
        payload={
            "message": {
                "type": "interactive",
                "interactive": {
                    "type": "list_reply",
                    "list_reply": {"id": button_id, "title": "x"},
                },
            }
        },
    )


def test_a_row_id_reads_back() -> None:
    visit_id, tag_id = uuid.uuid4(), uuid.uuid4()

    assert parse_try_row(tap(try_row_id(visit_id, tag_id))) == (visit_id, tag_id)


def test_a_row_id_fits_whatsapps_limit() -> None:
    assert len(try_row_id(uuid.uuid4(), uuid.uuid4())) <= payloads.MAX_LIST_ROW_ID_LENGTH


@pytest.mark.parametrize("row", ["login", "try:", "try:abc", "try:abc:def", "other:a:b", ""])
def test_other_buttons_are_not_menu_taps(row: str) -> None:
    assert parse_try_row(tap(row)) is None


def test_typed_text_is_not_a_menu_tap() -> None:
    assert parse_try_row(tap(try_row_id(uuid.uuid4(), uuid.uuid4()), kind="text")) is None


def test_names_that_fit_are_left_alone() -> None:
    tag = Tag(id=uuid.uuid4(), name="x" * payloads.MAX_LIST_ROW_TITLE_LENGTH)

    (row,) = try_rows(uuid.uuid4(), [tag])

    assert row.title == tag.name


def test_long_names_are_shortened_for_the_menu() -> None:
    tag = Tag(id=uuid.uuid4(), name="x" * 40)

    (row,) = try_rows(uuid.uuid4(), [tag])

    assert len(row.title) == payloads.MAX_LIST_ROW_TITLE_LENGTH
    assert row.title.endswith("…")


def test_list_message() -> None:
    payload = payloads.list_message(PHONE, "What?", "Choose", [ListRow("a", "Mocha")])

    assert payload["to"] == PHONE.removeprefix("+")
    assert payload["interactive"]["type"] == "list"
    assert payload["interactive"]["action"] == {
        "button": "Choose",
        "sections": [{"rows": [{"id": "a", "title": "Mocha"}]}],
    }


@pytest.mark.parametrize(
    ("body", "button", "rows"),
    [
        ("", "Choose", [ListRow("a", "A")]),
        ("x" * 1025, "Choose", [ListRow("a", "A")]),
        ("What?", "", [ListRow("a", "A")]),
        ("What?", "x" * 21, [ListRow("a", "A")]),
        ("What?", "Choose", []),
        ("What?", "Choose", [ListRow(str(n), "A") for n in range(11)]),
        ("What?", "Choose", [ListRow("a", "x" * 25)]),
        ("What?", "Choose", [ListRow("a", " ")]),
        ("What?", "Choose", [ListRow("", "A")]),
        ("What?", "Choose", [ListRow("x" * 201, "A")]),
        ("What?", "Choose", [ListRow("a", "A"), ListRow("a", "B")]),
    ],
)
def test_list_message_limits(body: str, button: str, rows: list[ListRow]) -> None:
    with pytest.raises(ValueError):
        payloads.list_message(PHONE, body, button, rows)


def test_the_simulator_shows_the_row_ids_to_tap() -> None:
    payload = payloads.list_message(PHONE, "What?", "Choose", [ListRow("try:1:2", "Mocha")])

    assert _text_of(payload) == "What?\n[Mocha] try:1:2"


def test_tag_name_is_trimmed() -> None:
    assert TagCreate(name="  Mocha ", kind="item").name == "Mocha"


@pytest.mark.parametrize("name", ["", "   ", "x" * 41, "☕"])
def test_bad_tag_names(name: str) -> None:
    with pytest.raises(ValidationError):
        TagCreate(name=name, kind="item")


def test_selection_limits() -> None:
    ids = [uuid.uuid4() for _ in range(MAX_VENUE_ITEMS + 1)]

    assert VenueTagsIn(tag_ids=ids[:MAX_VENUE_TAGS]).tag_ids
    assert VenueItemsIn(tag_ids=ids[:MAX_VENUE_ITEMS]).tag_ids
    with pytest.raises(ValidationError):
        VenueTagsIn(tag_ids=ids[: MAX_VENUE_TAGS + 1])
    with pytest.raises(ValidationError):
        VenueItemsIn(tag_ids=ids)


@pytest.mark.parametrize("schema", [VenueTagsIn, VenueItemsIn])
def test_repeats_are_refused(schema) -> None:
    tag_id = uuid.uuid4()

    with pytest.raises(ValidationError):
        schema(tag_ids=[tag_id, tag_id])
