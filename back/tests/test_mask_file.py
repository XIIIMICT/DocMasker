"""Tests for the /api/mask-file endpoint (multipart upload -> masked .docx download).

The GLiNER model is stubbed through `dependency_overrides`, so no network or
model download is needed (mirrors `test_mask.py`).
"""

from __future__ import annotations

import io
from urllib.parse import unquote

import httpx
from docx import Document as DocxDocument
from test_docx import StubDocxModel, _build_docx

from src.main import app
from src.masking import service as masking_service


def _stub_model() -> StubDocxModel:
    return StubDocxModel()


def _client() -> httpx.AsyncClient:
    app.dependency_overrides[masking_service.get_gliner_model] = _stub_model
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_mask_file_returns_masked_docx_and_headers() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={
                "file": (
                    "доклад.docx",
                    _build_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    # The count header and the RFC 5987 Cyrillic download name are advertised.
    assert int(response.headers["x-masked-count"]) >= 3
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert unquote(disposition.split("filename*=UTF-8''")[1]) == "доклад (замаскированный).docx"

    document = DocxDocument(io.BytesIO(response.content))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Иван Петров" not in text
    assert "[PERSON]" in text


async def test_mask_file_rejects_non_docx() -> None:
    async with _client() as client:
        response = await client.post("/api/mask-file", files={"file": ("notes.txt", b"hello", "text/plain")})

    assert response.status_code == 422
    assert response.json()["detail"] == "Файл должен иметь расширение .docx."


async def test_mask_file_rejects_empty_file() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={"file": ("empty.docx", b"", "application/octet-stream")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Файл пуст."


async def test_mask_text_rejects_blank_text() -> None:
    async with _client() as client:
        response = await client.post("/api/mask", json={"text": "   "}, headers={})

    assert response.status_code == 422
    assert response.json()["detail"] == "Текст пуст."


def teardown_module() -> None:
    app.dependency_overrides.clear()
