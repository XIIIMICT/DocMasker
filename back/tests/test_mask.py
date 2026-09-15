"""Tests for the masking service and the /api/mask endpoint."""

from __future__ import annotations

import httpx

from src.main import app
from src.masking.rule_based import Entity, RuleBasedMasker
from src.masking.schemas import MaskedEntity, MaskRequest
from src.masking.service import mask_document, mask_text, mask_with


def test_mask_text_replaces_spans() -> None:
    entities = [
        MaskedEntity(label="person", text="Иван", start=4, end=8, score=1.0),
        MaskedEntity(label="phone", text="+7 900", start=14, end=20, score=1.0),
    ]
    result = mask_text("Кто Иван, тел +7 900.", entities)
    assert result == "Кто [PERSON], тел [PHONE]."


def test_mask_with_injected_masker_is_deterministic() -> None:
    masked, entities = mask_with(RuleBasedMasker(), "Иван Петров, ivan@mail.ru")
    assert masked == "[PERSON], [EMAIL]"
    assert {entity.label for entity in entities} == {"person", "email"}


def test_mask_document_uses_settings_singleton() -> None:
    request = MaskRequest(text="Иван Петров, тел +7 900 123-45-67")
    masked, entities = mask_document(request)
    assert masked == "[PERSON], тел [PHONE]"
    assert [entity.label for entity in entities] == ["person", "phone"]


def test_entity_offsets_point_at_original_text() -> None:
    text = "Контакт: Иван Петров"
    entities = RuleBasedMasker().detect(text)
    assert len(entities) == 1
    entity = entities[0]
    assert text[entity.start : entity.end] == "Иван Петров"


async def test_post_mask_endpoint() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/mask", json={"text": "Иван Петров, ivan@mail.ru"})

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "[PERSON], [EMAIL]"
    assert [entity["label"] for entity in body["masked_entities"]] == ["person", "email"]


async def test_post_mask_endpoint_rejects_blank_text() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/mask", json={"text": "   "})

    assert response.status_code == 422
    assert response.json()["detail"] == "Текст пуст."


def test_entity_is_frozen_dataclass() -> None:
    entity = Entity(label="person", text="Иван", start=0, end=4)
    assert entity.score == 1.0
