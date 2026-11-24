"""Tests for the canonical label catalogue endpoint (Phase 4, §5.3)."""

from httpx2 import ASGITransport, AsyncClient

from src.main import app
from src.masking.config import get_config


async def test_labels_endpoint_publishes_the_single_catalogue() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/labels")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["labels"] == [{"id": label, "title": title} for label, title in get_config().labels.entries]
    assert "profiles" not in body


async def test_profiles_endpoint_is_gone() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/profiles")
    assert response.status_code == 404


async def test_mask_has_no_profile_field() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/mask", json={"text": "Иван Петров", "profile_id": "nope"})
    # profile_id is no longer part of the public contract; FastAPI ignores the
    # unknown field and the single configuration is used.
    assert response.status_code == 200
    assert response.json()["text"] == "[PERSON_1]"
