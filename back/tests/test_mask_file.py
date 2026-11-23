"""Tests for the /api/mask-file endpoint (multipart upload -> ZIP of masked .docx + keys)."""

import io
import json
import zipfile
from urllib.parse import unquote

from docx import Document as DocxDocument
from httpx2 import ASGITransport, AsyncClient
from test_docx import _build_docx

from src.config import get_settings
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


async def test_mask_file_counts_occurrences_across_paragraphs() -> None:
    # Regression: identical local spans in different paragraphs used to collapse
    # into one reported entity, undercounting X-Masked-Count / X-Masked-Breakdown.
    doc = DocxDocument()
    for index in range(4):
        doc.add_paragraph(f"Сотрудник {index}: Смирнова Анна Павловна")
    buffer = io.BytesIO()
    doc.save(buffer)

    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={
                "file": (
                    "список.docx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200
    assert int(response.headers["x-masked-count"]) == 4
    breakdown = json.loads(response.headers["x-masked-breakdown"])
    assert breakdown == {"person": 4}


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


async def test_mask_file_rejects_garbage_bytes_with_415() -> None:
    # Regression: garbage bytes used to escape the endpoint as a 500.
    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={
                "file": (
                    "мусор.docx",
                    b"definitely not a zip archive" * 100,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 415
    assert response.json()["detail"] == "Файл не является корректным DOCX (повреждённый архив)."


async def test_mask_file_rejects_non_docx_zip_with_415() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "valid zip, not a document")

    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={
                "file": (
                    "не-документ.docx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 415
    assert response.json()["detail"] == "Файл не является корректным DOCX (отсутствуют части пакета Word)."


async def test_mask_file_rejects_oversized_upload_with_413() -> None:
    # The endpoint must enforce its own limit (the front is not a trust boundary).
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/blob.xml", b"0" * (2 * 1024 * 1024))

    with OverrideSettings(max_upload_mb=1):
        async with _client() as client:
            response = await client.post(
                "/api/mask-file",
                files={
                    "file": (
                        "большой.docx",
                        buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )

    assert response.status_code == 413


class OverrideSettings:
    """Temporarily override attributes of the cached Settings (request-scoped limits)."""

    def __init__(self, **overrides: object) -> None:
        self.overrides = overrides
        self.original: dict[str, object] = {}

    def __enter__(self) -> None:
        settings = get_settings()
        self.original = {name: getattr(settings, name) for name in self.overrides}
        for name, value in self.overrides.items():
            setattr(settings, name, value)

    def __exit__(self, *exception: object) -> None:
        settings = get_settings()
        for name, value in self.original.items():
            setattr(settings, name, value)


async def test_unmask_file_returns_zip_of_docx_and_manifest() -> None:
    """The restored document and unused-keys manifest ride in one ZIP (no PII headers)."""
    masked_archive = await _mask_archive()
    with zipfile.ZipFile(io.BytesIO(masked_archive)) as archive:
        masked_docx = archive.read("отчёт (замаскированный).docx")
        keys_yaml = archive.read("отчёт (ключи демаскировки).yaml").decode("utf-8")

    async with _client() as client:
        response = await client.post(
            "/api/unmask-file",
            files={
                "file": (
                    "отчёт.docx",
                    masked_docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"keys": keys_yaml},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "x-unused-keys" not in response.headers
    disposition = response.headers["content-disposition"]
    assert unquote(disposition.split("filename*=UTF-8''")[1]) == "отчёт (демаскированный).zip"

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"отчёт (демаскированный).docx", "отчёт (отчёт о демаскировке).json"}
        restored_docx = archive.read("отчёт (демаскированный).docx")
        manifest = json.loads(archive.read("отчёт (отчёт о демаскировке).json").decode("utf-8"))

    assert manifest == {"unused_keys": []}
    document = DocxDocument(io.BytesIO(restored_docx))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Иван Петров" in text
    assert "[PERSON_1]" not in text


async def test_unmask_file_reports_unused_keys_in_manifest() -> None:
    masked_archive = await _mask_archive()
    with zipfile.ZipFile(io.BytesIO(masked_archive)) as archive:
        masked_docx = archive.read("отчёт (замаскированный).docx")
        keys_yaml = archive.read("отчёт (ключи демаскировки).yaml").decode("utf-8")
    keys_with_extra = keys_yaml + '\nСсылки:\n  - "https://never-mentioned.example"'

    async with _client() as client:
        response = await client.post(
            "/api/unmask-file",
            files={
                "file": (
                    "отчёт.docx",
                    masked_docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"keys": keys_with_extra},
        )

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("отчёт (отчёт о демаскировке).json").decode("utf-8"))
    # yaml12 strips the quotes on parse, so the value arrives unquoted.
    assert manifest == {"unused_keys": ["https://never-mentioned.example"]}


async def test_unmask_file_rejects_garbage_bytes_with_415() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/unmask-file",
            files={
                "file": (
                    "мусор.docx",
                    b"not a zip at all" * 50,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"keys": "Физлица:\n  - Иванов Иван Иванович"},
        )

    assert response.status_code == 415


async def _mask_archive() -> bytes:
    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={
                "file": (
                    "отчёт.docx",
                    _build_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert response.status_code == 200
    return response.content


async def test_mask_file_warns_about_unsupported_ooxml_parts() -> None:
    # A package with a comments part cannot be fully anonymized: the response
    # must carry a short, safe code list (no values, no filenames).
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as outer:
        inner = io.BytesIO(_build_docx())
        with zipfile.ZipFile(inner) as source:
            for info in source.infolist():
                outer.writestr(info, source.read(info.filename))
        outer.writestr("word/comments.xml", "<w:comments/>")

    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={
                "file": (
                    "doc.docx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert response.status_code == 200
    assert response.headers["x-docx-unsupported-parts"] == "comments"


async def test_mask_file_has_no_warning_for_a_plain_docx() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/mask-file",
            files={
                "file": (
                    "doc.docx",
                    _build_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert response.status_code == 200
    assert "x-docx-unsupported-parts" not in response.headers
