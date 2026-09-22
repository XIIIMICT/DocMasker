"""Tests for the demasking pipeline (keys round-trip, text and DOCX)."""

from __future__ import annotations

import io
import json
from urllib.parse import unquote

import httpx
from docx import Document as DocxDocument

from src.main import app
from src.masking.docx_service import mask_docx_in_memory, unmask_docx_in_memory
from src.masking.mapping import parse_keys_yaml, placeholders_from_document
from src.masking.rule_based import RuleBasedMasker
from src.masking.service import mask_with, unmask_text


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def test_mask_then_unmask_restores_the_text() -> None:
    original = "Иван Петров, тел +7 900 123-45-67, почта ivan@mail.ru"
    masked, _entities, keys = mask_with(RuleBasedMasker(), original)
    placeholders = placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    assert unmask_text(masked, placeholders) == original


def test_unmask_restores_each_declension_form() -> None:
    # Different wordings of one organisation must come back exactly as written.
    original = 'Работал в АО "Росхим", затем в Акционерного общества «Росхим».'
    masked, _entities, keys = mask_with(RuleBasedMasker(), original)
    assert "Росхим" not in masked
    placeholders = placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    assert unmask_text(masked, placeholders) == original


def test_unmask_reuses_one_key_for_a_repeated_value() -> None:
    original = "Звоните +79494271795 или снова +79494271795."
    masked, _entities, keys = mask_with(RuleBasedMasker(), original)
    assert masked.count("[PHONE_1]") == 2
    placeholders = placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    assert unmask_text(masked, placeholders) == original


def test_unknown_placeholder_is_left_untouched() -> None:
    assert unmask_text("[PERSON_9] и [PERSON_1]", {"[PERSON_1]": "Иван"}) == "[PERSON_9] и Иван"


def test_unmask_ignores_unknown_sections() -> None:
    # A foreign/newer keys file must not crash restoring; unknown titles are skipped.
    placeholders = placeholders_from_document({"Неизвестный раздел": ["x"], "Физлица": ["Иван"]})
    assert placeholders == {"[PERSON_1]": "Иван"}


def test_unmask_docx_restores_the_document() -> None:
    doc = DocxDocument()
    doc.add_paragraph("Доклад: Иван Петров, тел +7 900 123-45-67.")
    out = io.BytesIO()
    doc.save(out)

    masked_bytes, _entities, keys = mask_docx_in_memory(out.getvalue())
    placeholders = placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    restored, unused = unmask_docx_in_memory(masked_bytes, placeholders)

    text = "\n".join(p.text for p in DocxDocument(io.BytesIO(restored)).paragraphs)
    assert text == "Доклад: Иван Петров, тел +7 900 123-45-67."
    assert unused == []


def test_unmask_docx_restores_a_marker_split_across_runs() -> None:
    # Masking writes the marker into one run, but a marker that was split by an
    # outside edit must still collapse back into a single value.
    doc = DocxDocument()
    paragraph = doc.add_paragraph()
    paragraph.add_run("[PERSON_")
    paragraph.add_run("1] пришёл.")
    out = io.BytesIO()
    doc.save(out)

    restored, unused = unmask_docx_in_memory(out.getvalue(), {"[PERSON_1]": "Иван Петров"})
    text = DocxDocument(io.BytesIO(restored)).paragraphs[0].text
    assert text == "Иван Петров пришёл."
    assert unused == []


def test_unmask_docx_reports_keys_absent_from_the_document() -> None:
    doc = DocxDocument()
    doc.add_paragraph("[PERSON_1] пришёл.")
    out = io.BytesIO()
    doc.save(out)

    restored, unused = unmask_docx_in_memory(
        out.getvalue(),
        {"[PERSON_1]": "Иван Петров", "[ORGANIZATION_1]": "ООО «Рога и копыта»"},
    )
    assert unused == ["ООО «Рога и копыта»"]
    assert DocxDocument(io.BytesIO(restored)).paragraphs[0].text == "Иван Петров пришёл."


async def test_post_unmask_endpoint() -> None:
    async with _client() as client:
        masked = await client.post("/api/mask", json={"text": "Иван Петров, ivan@mail.ru"})
        body = masked.json()

        response = await client.post(
            "/api/unmask",
            json={"text": body["text"], "keys": body["keys"]},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "Иван Петров, ivan@mail.ru"


async def test_post_unmask_endpoint_rejects_blank_text() -> None:
    async with _client() as client:
        response = await client.post("/api/unmask", json={"text": "   ", "keys": "Физлица:\n  - A"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Текст пуст."


async def test_post_unmask_endpoint_rejects_invalid_keys() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/unmask",
            json={"text": "[PERSON_1]", "keys": "Телефоны:\n  - +7 900"},
        )

    assert response.status_code == 422
    assert "Неизвестный раздел" in response.json()["detail"]


async def test_post_unmask_file_endpoint_rejects_invalid_keys() -> None:
    doc = DocxDocument()
    doc.add_paragraph("[PERSON_1] пришёл.")
    out = io.BytesIO()
    doc.save(out)

    async with _client() as client:
        response = await client.post(
            "/api/unmask-file",
            files={"file": ("masked.docx", out.getvalue(), "application/octet-stream")},
            data={"keys": "- не словарь\n"},
        )

    assert response.status_code == 422
    assert "словарь разделов" in response.json()["detail"]


async def test_post_unmask_file_endpoint() -> None:
    doc = DocxDocument()
    doc.add_paragraph("Контакты: Иван Петров, тел +7 900 123-45-67.")
    out = io.BytesIO()
    doc.save(out)
    masked_bytes, _entities, keys = mask_docx_in_memory(out.getvalue())

    async with _client() as client:
        response = await client.post(
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

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    text = "\n".join(p.text for p in DocxDocument(io.BytesIO(response.content)).paragraphs)
    assert text == "Контакты: Иван Петров, тел +7 900 123-45-67."
    # Every key was applied.
    assert json.loads(unquote(response.headers["x-unused-keys"])) == []


async def test_post_unmask_file_endpoint_reports_unused_keys() -> None:
    doc = DocxDocument()
    doc.add_paragraph("[PERSON_1] пришёл.")
    out = io.BytesIO()
    doc.save(out)

    keys = "Физлица:\n  - Иван Петров\nОрганизации:\n  - ООО «Рога и копыта»"
    async with _client() as client:
        response = await client.post(
            "/api/unmask-file",
            files={"file": ("masked.docx", out.getvalue(), "application/octet-stream")},
            data={"keys": keys},
        )

    assert response.status_code == 200
    # The header is percent-encoded (latin-1 safe); decode it back.
    unused = json.loads(unquote(response.headers["x-unused-keys"]))
    assert unused == ["ООО «Рога и копыта»"]
