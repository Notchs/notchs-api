from httpx import AsyncClient


async def test_validation_error_names_the_field_without_echoing_input(client: AsyncClient) -> None:
    response = await client.post(
        "/venue/auth/login", json={"email": "not-an-email", "password": "my secret password"}
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"][0]["loc"] == ["body", "email"]
    assert "my secret password" not in response.text
    assert "not-an-email" not in response.text
