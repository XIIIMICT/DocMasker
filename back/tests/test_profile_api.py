"""Tests for the profile API surface (GET /api/profiles, profile_id selection)."""

from httpx2 import ASGITransport, AsyncClient

from src.main import app


def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_profiles_endpoint_lists_the_default_profile() -> None:
    async with _client() as client:
        response = await client.get("/api/profiles")

    assert response.status_code == 200
    body = response.json()
    assert body["profiles"][0]["profile_id"] == "russian-default"
    assert body["profiles"][0]["version"] == 1
    label_ids = [label["id"] for label in body["profiles"][0]["labels"]]
    assert label_ids == ["person", "phone", "email", "link", "address", "organization"]
    assert body["profiles"][0]["labels"][0]["title"] == "Физлица"


async def test_mask_accepts_an_explicit_profile_id() -> None:
    async with _client() as client:
        response = await client.post("/api/mask", json={"text": "Иван Петров", "profile_id": "russian-default"})

    assert response.status_code == 200
    assert response.json()["text"] == "[PERSON_1]"


async def test_mask_rejects_an_unknown_profile_id() -> None:
    async with _client() as client:
        response = await client.post("/api/mask", json={"text": "Иван Петров", "profile_id": "nope"})

    assert response.status_code == 422
    assert "Неизвестный профиль" in response.json()["detail"]


async def test_mask_file_rejects_an_unknown_profile_id() -> None:
    docx_bytes = b"PK\x05\x06" + b"\x00" * 18  # structurally empty zip -> 415 after profile check

    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={"file": ("doc.docx", docx_bytes, "application/octet-stream")},
            data={"profile_id": "nope"},
        )

    # The unknown profile must fail with 422 before the package is even parsed.
    assert response.status_code == 422
    assert "Неизвестный профиль" in response.json()["detail"]
