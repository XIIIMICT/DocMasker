"""Tests for the /api/mask-file endpoint (multipart upload -> ZIP of masked .docx + keys)."""

import io
import json
import zipfile
from urllib.parse import unquote

from docx import Document as DocxDocument
from httpx2 import ASGITransport, AsyncClient
from test_docx import _build_docx

from src.main import app
from src.masking.mapping import parse_keys_yaml


def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_mask_file_returns_zip_of_docx_and_keys() -> None:
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
    assert response.headers["content-type"].startswith("application/zip")
    # The count header and the RFC 5987 Cyrillic download name are advertised.
    assert int(response.headers["x-masked-count"]) >= 4
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert unquote(disposition.split("filename*=UTF-8''")[1]) == "доклад (замаскированный).zip"

    # Per-label breakdown lets the front render the summary in the result label.
    breakdown = json.loads(response.headers["x-masked-breakdown"])
    assert breakdown["person"] == 3
    assert breakdown["phone"] == 2
    assert sum(breakdown.values()) == int(response.headers["x-masked-count"])

    # The archive carries the masked document and its keys, unpacked by the front.
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"доклад (замаскированный).docx", "доклад (ключи демаскировки).yaml"}
        masked_docx = archive.read("доклад (замаскированный).docx")
        keys_yaml = archive.read("доклад (ключи демаскировки).yaml").decode("utf-8")

    document = DocxDocument(io.BytesIO(masked_docx))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Иван Петров" not in text
    assert "[PERSON_1]" in text

    keys = parse_keys_yaml(keys_yaml)
    assert keys["Физлица"] == ["Иван Петров", "Иван Петрова"]
    assert keys["Номера телефонов"] == ["+7 900 123-45-67"]


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
