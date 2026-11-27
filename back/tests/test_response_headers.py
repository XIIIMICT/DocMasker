"""Wire cache policy: every `/api/*` response is no-store; route headers survive."""

import io

from docx import Document as DocxDocument
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient, Response
from test_docx import _build_docx

from src.errors import install_error_handlers
from src.main import app
from src.masking.docx_service import mask_docx_in_memory


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _assert_no_store(response: Response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-request-id"]


async def test_pii_json_endpoints_are_no_store() -> None:
    """Masked/restored text and keys are PII-derived: never cacheable."""
    async with _client() as client:
        masked = await client.post("/api/mask", json={"text": "Иван Петров"})
        restored = await client.post(
            "/api/unmask",
            json={"text": "[PERSON_1]", "keys": "Физлица:\n  - Иван Петров"},
        )

    assert masked.status_code == 200
    assert restored.status_code == 200
    for response in (masked, restored):
        assert response.headers["content-type"].startswith("application/json")
        _assert_no_store(response)


async def test_zip_artifact_endpoints_keep_disposition_and_add_pragma() -> None:
    """The existing ZIP `no-store` must not be dropped by the new middleware."""
    document = DocxDocument()
    document.add_paragraph("Контакты: Иван Петров.")
    buffer = io.BytesIO()
    document.save(buffer)
    masked_bytes, _entities, keys, _coverage = mask_docx_in_memory(buffer.getvalue())

    async with _client() as client:
        mask_response = await client.post(
            "/api/mask-file",
            files={"file": ("доклад.docx", _build_docx(), "application/octet-stream")},
        )
        unmask_response = await client.post(
            "/api/unmask-file",
            files={
                "file": (
                    "доклад (замаскированный).docx",
                    masked_bytes,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"keys": keys.to_yaml()},
        )

    assert mask_response.status_code == 200
    assert unmask_response.status_code == 200
    for response in (mask_response, unmask_response):
        assert response.headers["content-type"].startswith("application/zip")
        assert "filename*=UTF-8''" in response.headers["content-disposition"]
        _assert_no_store(response)


async def test_api_error_envelope_is_no_store() -> None:
    """A typed 422 from a PII endpoint carries the same wire policy."""
    async with _client() as client:
        response = await client.post("/api/mask", json={"text": "  "})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"code", "detail", "request_id"}
    assert body["request_id"] == response.headers["x-request-id"]
    _assert_no_store(response)


async def test_unexpected_api_500_keeps_no_store() -> None:
    """The outer sanitized 500 also carries the wire policy (no PII leaked)."""
    probe = FastAPI()
    install_error_handlers(probe)

    @probe.get("/api/boom")
    async def boom() -> None:
        msg = "/var/secrets/db.py ivan@mail.ru"
        raise RuntimeError(msg)

    transport = ASGITransport(app=probe, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "/var/secrets" not in response.text
    _assert_no_store(response)


async def test_non_api_probe_is_not_marked_no_store() -> None:
    """Scope is `/api/*`: liveness and generated docs keep their default headers."""
    async with _client() as client:
        health = await client.get("/health")
        schema = await client.get("/openapi.json")

    assert health.status_code == 200
    assert schema.status_code == 200
    for response in (health, schema):
        assert "cache-control" not in response.headers
        assert "pragma" not in response.headers
