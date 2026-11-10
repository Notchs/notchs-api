import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import Area, City

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def cities(db: AsyncSession) -> tuple[City, City]:
    live = City(
        name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat", is_active=True
    )
    soon = City(name="Surat", slug=f"srt-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add_all([live, soon])
    await db.flush()
    db.add_all(
        [
            Area(city_id=live.id, name="Navrangpura", slug="navrangpura"),
            Area(city_id=live.id, name="Bodakdev", slug="bodakdev"),
            Area(city_id=soon.id, name="Adajan", slug="adajan"),
        ]
    )
    await db.commit()
    return live, soon


async def test_venue_categories(api: AsyncClient) -> None:
    response = await api.get("/dropdowns/venue_categories")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.json()["data"] == [
        {"value": "cafe", "label": "Cafe"},
        {"value": "restaurant", "label": "Restaurant"},
        {"value": "bar", "label": "Bar"},
        {"value": "hotel", "label": "Hotel"},
        {"value": "turf_gym", "label": "Turf / Box ground / Gym"},
        {"value": "theatre", "label": "Theatre"},
        {"value": "other", "label": "Other"},
    ]


async def test_cities_lists_only_active_ones(api: AsyncClient, cities) -> None:
    live, _ = cities

    data = (await api.get("/dropdowns/cities")).json()["data"]

    assert {"value": str(live.id), "label": "Ahmedabad"} in data
    assert "Surat" not in [option["label"] for option in data]


async def test_areas_of_one_city_sorted_by_name(api: AsyncClient, cities) -> None:
    live, _ = cities

    data = (await api.get("/dropdowns/areas", params={"city_id": str(live.id)})).json()["data"]

    assert [option["label"] for option in data] == ["Bodakdev", "Navrangpura"]


@pytest.mark.parametrize("params", [{}, {"city_id": "not-a-uuid"}])
async def test_areas_need_a_valid_city(api: AsyncClient, params: dict[str, str]) -> None:
    response = await api.get("/dropdowns/areas", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dropdowns.filter_invalid"


async def test_unknown_dropdown(api: AsyncClient) -> None:
    response = await api.get("/dropdowns/planets")

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "dropdowns.not_found"
    assert {"areas", "cities", "venue_categories"} <= set(body["details"]["available"])


async def test_several_in_one_call(api: AsyncClient, cities) -> None:
    live, _ = cities

    response = await api.get(
        "/dropdowns",
        params={"names": "cities, venue_categories,areas,cities", "city_id": str(live.id)},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert list(data) == ["cities", "venue_categories", "areas"]
    assert len(data["venue_categories"]) == 7


@pytest.mark.parametrize(
    "names", ["", ",,", ",".join(["cities"] * 1 + [f"x{i}" for i in range(10)])]
)
async def test_several_needs_1_to_10_names(api: AsyncClient, names: str) -> None:
    response = await api.get("/dropdowns", params={"names": names})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dropdowns.filter_invalid"
