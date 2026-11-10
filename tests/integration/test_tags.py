"""Scans step 6 on a real database: the admins' tags, venues' lists and "What did you try?"."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin_users.models import AdminRole
from app.modules.scans import replies
from app.modules.scans.models import PointsEntry, Visit
from app.modules.tags.models import Tag, TagKind, VenueItem, VenueTag, VisitTag
from app.modules.tags.repository import TagRepository
from app.modules.tags.schemas import MAX_VENUE_ITEMS, MAX_VENUE_TAGS
from app.modules.tags.service import TagService
from app.modules.users.consent import consent_text
from app.modules.venues.models import Venue
from app.modules.whatsapp.bot import CONSENT_BUTTON, try_row_id
from tests.integration.test_admin import make_admin
from tests.integration.test_counter import owner  # noqa: F401  (fixture)
from tests.integration.test_scan_bot import (
    FakeMeta,
    add_customer,
    bot_under_test,  # noqa: F401  (fixture)
    code_now,
    meta,  # noqa: F401  (fixture)
    receive,
    scan_text,
)
from tests.integration.test_scan_pipeline import add_venue

pytestmark = pytest.mark.asyncio(loop_scope="session")


def unique(name: str) -> str:
    return f"{name} {uuid.uuid4().hex[:6]}"


@pytest.fixture
async def admin(db, redis, api) -> dict[str, str]:
    return await make_admin(db, redis, api)


async def add_tag(db: AsyncSession, name: str = "Mocha", kind: TagKind = TagKind.ITEM) -> Tag:
    named = unique(name)
    tag = Tag(name=named, slug=named.lower().replace(" ", "-"), kind=kind)
    db.add(tag)
    await db.flush()
    return tag


# --- admins ---------------------------------------------------------------------------------------


async def test_admin_adds_a_tag(api: AsyncClient, admin) -> None:
    name = unique("Cold Coffee")

    response = await api.post("/admin/tags", json={"name": name, "kind": "item"}, cookies=admin)

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["name"] == name
    assert data["slug"] == name.lower().replace(" ", "-")
    assert data["kind"] == "item"


async def test_duplicate_name_is_refused_in_any_case(api: AsyncClient, admin) -> None:
    name = unique("Matcha")
    await api.post("/admin/tags", json={"name": name, "kind": "item"}, cookies=admin)

    response = await api.post(
        "/admin/tags", json={"name": f"  {name.upper()} ", "kind": "vibe"}, cookies=admin
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tags.name_taken"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "kind": "item"},
        {"name": "x" * 41, "kind": "item"},
        {"name": "☕☕", "kind": "item"},
        {"name": "Mocha", "kind": "nonsense"},
        {"name": "Mocha"},
        {"kind": "item"},
    ],
)
async def test_invalid_tags_are_refused(api: AsyncClient, admin, body) -> None:
    response = await api.post("/admin/tags", json=body, cookies=admin)

    assert response.status_code == 422


async def test_list_filters_by_kind_and_get_one(api: AsyncClient, db, admin) -> None:
    item = await add_tag(db, "Pasta")
    vibe = await add_tag(db, "Late Night", TagKind.VIBE)
    await db.commit()

    response = await api.get("/admin/tags", params={"kind": "vibe", "size": 100}, cookies=admin)

    ids = {row["id"] for row in response.json()["data"]["items"]}
    assert str(vibe.id) in ids and str(item.id) not in ids
    one = await api.get(f"/admin/tags/{item.id}", cookies=admin)
    assert one.json()["data"]["name"] == item.name


async def test_get_unknown_tag(api: AsyncClient, admin) -> None:
    response = await api.get(f"/admin/tags/{uuid.uuid4()}", cookies=admin)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "tags.not_found"


async def test_rename_updates_the_slug(api: AsyncClient, db, admin) -> None:
    tag = await add_tag(db)
    await db.commit()
    name = unique("Hot Mocha")

    response = await api.patch(f"/admin/tags/{tag.id}", json={"name": name}, cookies=admin)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["slug"] == name.lower().replace(" ", "-")


async def test_rename_to_a_taken_name(api: AsyncClient, db, admin) -> None:
    first, second = await add_tag(db), await add_tag(db)
    await db.commit()

    response = await api.patch(f"/admin/tags/{first.id}", json={"name": second.name}, cookies=admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tags.name_taken"


async def test_kind_changes_only_while_unused(api: AsyncClient, db, admin) -> None:
    tag = await add_tag(db)
    venue, _ = await add_venue(db)
    await db.commit()

    free = await api.patch(f"/admin/tags/{tag.id}", json={"kind": "vibe"}, cookies=admin)
    assert free.status_code == 200, free.text

    db.add(VenueTag(venue_id=venue.id, tag_id=tag.id))
    await db.commit()
    locked = await api.patch(f"/admin/tags/{tag.id}", json={"kind": "item"}, cookies=admin)
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "tags.kind_locked"


async def test_delete_takes_it_off_venue_lists(api: AsyncClient, db, admin) -> None:
    tag = await add_tag(db)
    venue, _ = await add_venue(db)
    db.add(VenueItem(venue_id=venue.id, tag_id=tag.id, position=0))
    await db.commit()

    response = await api.delete(f"/admin/tags/{tag.id}", cookies=admin)

    assert response.status_code == 204
    assert await TagRepository(db).venue_items(venue.id) == []
    gone = await api.get(f"/admin/tags/{tag.id}", cookies=admin)
    assert gone.status_code == 404


async def test_delete_is_refused_once_a_customer_picked_it(
    api: AsyncClient,
    db,
    admin,
    bot_under_test,  # noqa: F811
) -> None:
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    response = await api.delete(f"/admin/tags/{tag.id}", cookies=admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tags.in_use"


async def test_the_database_also_refuses_deleting_a_picked_tag(db, bot_under_test) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    with pytest.raises(IntegrityError, match="fk_visit_tags_tag_id_tags"):
        async with db.begin_nested():
            await db.delete(tag)
            await db.flush()


async def test_only_admins_change_tags(api: AsyncClient, db, redis, owner) -> None:  # noqa: F811
    tag_id = (await add_tag(db)).id  # kept: a refused request rolls the shared session back
    await db.commit()
    body = {"name": unique("Tea"), "kind": "item"}

    api.cookies.clear()
    assert (await api.post("/admin/tags", json=body)).status_code == 401
    venue_cookies = owner["cookies"]
    assert (await api.post("/admin/tags", json=body, cookies=venue_cookies)).status_code == 401

    reviewer = await make_admin(db, redis, api, AdminRole.REVIEWER)
    assert (await api.get("/admin/tags", cookies=reviewer)).status_code == 200
    assert (await api.post("/admin/tags", json=body, cookies=reviewer)).status_code == 403
    patched = await api.patch(f"/admin/tags/{tag_id}", json={"name": "x1"}, cookies=reviewer)
    assert patched.status_code == 403
    assert (await api.delete(f"/admin/tags/{tag_id}", cookies=reviewer)).status_code == 403


# --- the dropdown ---------------------------------------------------------------------------------


async def test_dropdown_filters_by_kind(api: AsyncClient, db) -> None:
    item = await add_tag(db, "Burger")
    vibe = await add_tag(db, "Outdoor", TagKind.VIBE)
    await db.commit()

    items = (await api.get("/dropdowns/tags", params={"kind": "item"})).json()["data"]
    everything = (await api.get("/dropdowns/tags")).json()["data"]

    assert {"value": str(item.id), "label": item.name} in items
    assert str(vibe.id) not in {row["value"] for row in items}
    assert str(vibe.id) in {row["value"] for row in everything}


async def test_dropdown_refuses_an_unknown_kind(api: AsyncClient) -> None:
    assert (await api.get("/dropdowns/tags", params={"kind": "nope"})).status_code == 422


# --- venues ---------------------------------------------------------------------------------------


async def test_venue_sets_its_items_in_order(api: AsyncClient, db, owner) -> None:  # noqa: F811
    first, second, third = [await add_tag(db) for _ in range(3)]
    await db.commit()
    wanted = [str(third.id), str(first.id), str(second.id)]

    saved = await api.put("/venue/items", json={"tag_ids": wanted}, cookies=owner["cookies"])

    assert saved.status_code == 200, saved.text
    assert [row["id"] for row in saved.json()["data"]] == wanted
    read = await api.get("/venue/items", cookies=owner["cookies"])
    assert [row["id"] for row in read.json()["data"]] == wanted


async def test_saving_replaces_the_list(api: AsyncClient, db, owner) -> None:  # noqa: F811
    old, new = await add_tag(db), await add_tag(db)
    await db.commit()
    cookies = owner["cookies"]
    await api.put("/venue/items", json={"tag_ids": [str(old.id), str(new.id)]}, cookies=cookies)

    saved = await api.put("/venue/items", json={"tag_ids": [str(new.id)]}, cookies=cookies)
    assert [row["id"] for row in saved.json()["data"]] == [str(new.id)]

    emptied = await api.put("/venue/items", json={"tag_ids": []}, cookies=cookies)
    assert emptied.json()["data"] == []


async def test_venue_sets_its_tags(api: AsyncClient, db, owner) -> None:  # noqa: F811
    vibe = await add_tag(db, "Date Spot", TagKind.VIBE)
    cuisine = await add_tag(db, "Italian", TagKind.CUISINE)
    await db.commit()
    wanted = [str(vibe.id), str(cuisine.id)]

    saved = await api.put("/venue/tags", json={"tag_ids": wanted}, cookies=owner["cookies"])

    assert saved.status_code == 200, saved.text
    assert {row["id"] for row in saved.json()["data"]} == set(wanted)
    read = await api.get("/venue/tags", cookies=owner["cookies"])
    assert {row["id"] for row in read.json()["data"]} == set(wanted)


async def test_the_limits(api: AsyncClient, db, owner) -> None:  # noqa: F811
    cookies = owner["cookies"]
    vibes = [str((await add_tag(db, "V", TagKind.VIBE)).id) for _ in range(MAX_VENUE_TAGS + 1)]
    items = [str((await add_tag(db)).id) for _ in range(MAX_VENUE_ITEMS + 1)]
    await db.commit()

    assert (
        await api.put("/venue/tags", json={"tag_ids": vibes}, cookies=cookies)
    ).status_code == 422
    assert (
        await api.put("/venue/items", json={"tag_ids": items}, cookies=cookies)
    ).status_code == 422
    fine = await api.put("/venue/tags", json={"tag_ids": vibes[:-1]}, cookies=cookies)
    assert fine.status_code == 200
    fine = await api.put("/venue/items", json={"tag_ids": items[:-1]}, cookies=cookies)
    assert fine.status_code == 200


async def test_the_same_tag_twice_is_refused(api: AsyncClient, db, owner) -> None:  # noqa: F811
    tag = await add_tag(db)
    await db.commit()

    response = await api.put(
        "/venue/items", json={"tag_ids": [str(tag.id), str(tag.id)]}, cookies=owner["cookies"]
    )

    assert response.status_code == 422


async def test_wrong_kinds_and_unknown_tags_are_refused(api: AsyncClient, db, owner) -> None:  # noqa: F811
    cookies = owner["cookies"]
    item = await add_tag(db)
    vibe = await add_tag(db, "Family", TagKind.VIBE)
    await db.commit()

    cases = [
        ("/venue/items", str(vibe.id)),
        ("/venue/tags", str(item.id)),
        ("/venue/items", str(uuid.uuid4())),
    ]
    for path, tag_id in cases:
        response = await api.put(path, json={"tag_ids": [tag_id]}, cookies=cookies)
        assert response.status_code == 400, path
        assert response.json()["error"]["code"] == "tags.selection_invalid"


async def test_a_venue_only_changes_its_own_lists(api: AsyncClient, db, owner) -> None:  # noqa: F811
    other, _ = await add_venue(db)
    theirs = await add_tag(db)
    db.add(VenueItem(venue_id=other.id, tag_id=theirs.id, position=0))
    mine = await add_tag(db)
    await db.commit()

    await api.put("/venue/items", json={"tag_ids": [str(mine.id)]}, cookies=owner["cookies"])

    assert [tag.id for tag in await TagRepository(db).venue_items(other.id)] == [theirs.id]
    read = await api.get("/venue/items", cookies=owner["cookies"])
    assert [row["id"] for row in read.json()["data"]] == [str(mine.id)]


async def test_venue_lists_need_the_owner_login(api: AsyncClient, admin) -> None:
    api.cookies.clear()
    assert (await api.get("/venue/items")).status_code == 401
    assert (await api.put("/venue/tags", json={"tag_ids": []})).status_code == 401
    assert (await api.get("/venue/items", cookies=admin)).status_code == 401


# --- "What did you try?" --------------------------------------------------------------------------


def _row(visit: Visit, tag: Tag) -> str:
    return try_row_id(visit.id, tag.id)


async def visit_with_menu(db, bot, *, items: int = 1, agreed: bool = True):
    """A customer scanned at a venue with a menu. Returns (first tag, their visit, customer)."""
    venue, secret = await add_venue(db)
    tags = [await add_tag(db) for _ in range(items)]
    db.add_all(
        VenueItem(venue_id=venue.id, tag_id=tag.id, position=position)
        for position, tag in enumerate(tags)
    )
    user = await add_customer(db, agreed=agreed)
    await db.commit()
    await receive(db, bot, user.phone_e164, scan_text(venue, code_now(secret)))
    visit = await db.scalar(select(Visit).where(Visit.user_id == user.id))
    return tags[0], visit, user


async def answers(db: AsyncSession, visit: Visit) -> list[uuid.UUID]:
    rows = await db.scalars(select(VisitTag.tag_id).where(VisitTag.visit_id == visit.id))
    return list(rows.all())


def menu_rows(meta: FakeMeta) -> list[dict[str, str]]:  # noqa: F811
    return meta.sent[-1]["interactive"]["action"]["sections"][0]["rows"]


async def test_the_stamp_is_followed_by_the_venues_menu(db, bot_under_test, meta) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    tags = [await add_tag(db) for _ in range(2)]
    db.add_all(
        [
            VenueItem(venue_id=venue.id, tag_id=tags[1].id, position=0),
            VenueItem(venue_id=venue.id, tag_id=tags[0].id, position=1),
        ]
    )
    user = await add_customer(db)
    await db.commit()

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    stamp, question = meta.texts()
    assert stamp.startswith("Stamp added")
    assert question == replies.TRY_QUESTION
    assert meta.sent[-1]["interactive"]["type"] == "list"
    visit = await db.scalar(select(Visit).where(Visit.user_id == user.id))
    assert menu_rows(meta) == [
        {"id": _row(visit, tags[1]), "title": tags[1].name},
        {"id": _row(visit, tags[0]), "title": tags[0].name},
    ]


async def test_no_menu_when_the_venue_has_no_items(db, bot_under_test, meta) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    await db.commit()

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert len(meta.sent) == 1


async def test_first_scan_gets_consent_and_no_menu(db, bot_under_test, meta) -> None:  # noqa: F811
    await visit_with_menu(db, bot_under_test, agreed=False)

    stamp, question = meta.texts()
    assert stamp.startswith("Stamp added")
    assert question == consent_text()


async def test_after_agreeing_the_next_visit_gets_the_menu(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, _, user = await visit_with_menu(db, bot_under_test, agreed=False)
    await receive(
        db, bot_under_test, user.phone_e164, CONSENT_BUTTON.title, button_id=CONSENT_BUTTON.id
    )
    meta.sent.clear()
    venue, secret = await add_venue(db, category="restaurant")  # a café was earlier today
    db.add(VenueItem(venue_id=venue.id, tag_id=tag.id, position=0))
    await db.commit()

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert meta.texts()[-1] == replies.TRY_QUESTION


async def test_no_menu_after_a_refused_scan(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, _, user = await visit_with_menu(db, bot_under_test)
    venue_id = (await db.scalar(select(VenueItem).where(VenueItem.tag_id == tag.id))).venue_id
    venue = await db.get(Venue, venue_id)
    meta.sent.clear()

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, "000000"))

    assert all(payload["type"] != "interactive" for payload in meta.sent)


async def test_a_long_item_name_is_shortened_only_in_the_menu(db, bot_under_test, meta) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    tag = await add_tag(db, "Extra Large Iced Caramel Mocha")
    db.add(VenueItem(venue_id=venue.id, tag_id=tag.id, position=0))
    user = await add_customer(db)
    await db.commit()

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    title = menu_rows(meta)[0]["title"]
    assert len(title) == 24 and title.endswith("…")
    visit = await db.scalar(select(Visit).where(Visit.user_id == user.id))
    await receive(db, bot_under_test, user.phone_e164, title, button_id=_row(visit, tag))
    assert meta.texts()[-1] == replies.tried_text(tag.name)


async def test_a_tap_saves_the_answer_and_gives_no_points(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    points = select(func.sum(PointsEntry.points)).where(PointsEntry.user_id == user.id)
    before = await db.scalar(points)

    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    assert await answers(db, visit) == [tag.id]
    assert meta.texts()[-1] == replies.tried_text(tag.name)
    assert await db.scalar(points) == before


async def test_several_answers_are_fine(db, bot_under_test, meta) -> None:  # noqa: F811
    first, visit, user = await visit_with_menu(db, bot_under_test, items=2)
    second = (await TagRepository(db).venue_items(visit.venue_id))[1]

    await receive(db, bot_under_test, user.phone_e164, first.name, button_id=_row(visit, first))
    await receive(db, bot_under_test, user.phone_e164, second.name, button_id=_row(visit, second))

    assert set(await answers(db, visit)) == {first.id, second.id}
    assert meta.texts()[-2:] == [replies.tried_text(first.name), replies.tried_text(second.name)]


async def test_a_double_tap_saves_once_and_replies_once(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))
    sent = len(meta.sent)

    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    assert await answers(db, visit) == [tag.id]
    assert len(meta.sent) == sent


async def test_another_customers_visit_is_closed(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, _ = await visit_with_menu(db, bot_under_test)
    someone_else = await add_customer(db)

    await receive(db, bot_under_test, someone_else.phone_e164, tag.name, button_id=_row(visit, tag))

    assert await answers(db, visit) == []
    assert meta.texts()[-1] == replies.TRY_VISIT_CLOSED


async def test_an_item_no_longer_on_the_menu(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    await TagRepository(db).replace_venue_items(visit.venue_id, [])
    await db.commit()

    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    assert await answers(db, visit) == []
    assert meta.texts()[-1] == replies.TRY_NOT_ON_MENU


async def test_another_venues_item_is_refused(db, bot_under_test, meta) -> None:  # noqa: F811
    _, visit, user = await visit_with_menu(db, bot_under_test)
    other_item = await add_tag(db)
    await db.commit()

    await receive(
        db, bot_under_test, user.phone_e164, other_item.name, button_id=_row(visit, other_item)
    )

    assert await answers(db, visit) == []
    assert meta.texts()[-1] == replies.TRY_NOT_ON_MENU


async def test_answers_stop_after_24_hours(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    service = TagService(db)

    in_time = await service.record_tried(
        user, visit.id, tag.id, now=visit.visited_at + timedelta(hours=24)
    )
    assert in_time.outcome == "saved"

    second = (await add_tag(db)).id
    db.add(VenueItem(venue_id=visit.venue_id, tag_id=second, position=1))
    await db.flush()
    too_late = await service.record_tried(
        user, visit.id, second, now=visit.visited_at + timedelta(hours=24, seconds=1)
    )
    assert too_late.outcome == "visit_closed"


async def test_an_old_menu_gets_the_closed_reply(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    long_ago = datetime.now(UTC) - timedelta(days=2)
    await db.execute(update(Visit).where(Visit.id == visit.id).values(visited_at=long_ago))
    await db.commit()
    await db.refresh(visit)

    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    assert await answers(db, visit) == []
    assert meta.texts()[-1] == replies.TRY_VISIT_CLOSED


async def test_a_revoked_visit_is_closed(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test)
    visit.status = "revoked"
    await db.commit()

    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    assert await answers(db, visit) == []


async def test_nothing_is_saved_before_they_agree(db, bot_under_test, meta) -> None:  # noqa: F811
    tag, visit, user = await visit_with_menu(db, bot_under_test, agreed=False)

    await receive(db, bot_under_test, user.phone_e164, tag.name, button_id=_row(visit, tag))

    assert await answers(db, visit) == []
    assert meta.texts()[-1] == replies.TRY_VISIT_CLOSED


async def test_closed_reply_at_most_once_a_minute(db, bot_under_test, meta, redis) -> None:  # noqa: F811
    tag, visit, _ = await visit_with_menu(db, bot_under_test)
    someone_else = await add_customer(db)
    tap = {"body": tag.name, "button_id": _row(visit, tag)}

    await receive(db, bot_under_test, someone_else.phone_e164, **tap)
    sent = len(meta.sent)
    await receive(db, bot_under_test, someone_else.phone_e164, **tap)

    assert len(meta.sent) == sent


@pytest.mark.parametrize("row", ["try:", "try:not-a-uuid:x", "try:only-one"])
async def test_a_malformed_row_gets_help_not_an_error(db, bot_under_test, meta, row) -> None:  # noqa: F811
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, "x", button_id=row)

    assert len(meta.sent) == 1
    assert meta.sent[0]["type"] == "interactive"
