"""Tests for the masking service and the /api/mask endpoint."""

from pathlib import Path

import pytest
from httpx2 import ASGITransport, AsyncClient

from src.main import app
from src.masking.mapping import MaskKeys, parse_keys_yaml
from src.masking.rule_based import Entity, RuleBasedMasker
from src.masking.schemas import MaskedEntity, MaskRequest
from src.masking.service import mask_document, mask_text, mask_with


def test_mask_text_replaces_spans_with_numbered_markers() -> None:
    entities = [
        MaskedEntity(label="person", text="Иван", start=4, end=8, score=1.0),
        MaskedEntity(label="phone", text="+7 900", start=14, end=20, score=1.0),
    ]
    result = mask_text("Кто Иван, тел +7 900.", entities, MaskKeys())
    assert result == "Кто [PERSON_1], тел [PHONE_1]."


def test_repeated_same_label_gets_ascending_numbers() -> None:
    # Two different people must not collapse into one placeholder, or the
    # document could not be restored.
    masked, _entities, keys = mask_with(RuleBasedMasker(), "Иван Петров и Пётр Сидоров")
    assert masked == "[PERSON_1] и [PERSON_2]"
    assert parse_keys_yaml(keys.to_yaml()) == {"Физлица": ["Иван Петров", "Пётр Сидоров"]}


def test_mask_with_injected_masker_is_deterministic() -> None:
    masked, entities, keys = mask_with(RuleBasedMasker(), "Иван Петров, ivan@mail.ru")
    assert masked == "[PERSON_1], [EMAIL_1]"
    assert {entity.label for entity in entities} == {"person", "email"}
    assert parse_keys_yaml(keys.to_yaml()) == {
        "Физлица": ["Иван Петров"],
        "Email": ["ivan@mail.ru"],
    }


def test_mask_document_uses_settings_singleton() -> None:
    request = MaskRequest(text="Иван Петров, тел +7 900 123-45-67")
    masked, entities, keys = mask_document(request)
    assert masked == "[PERSON_1], тел [PHONE_1]"
    assert [entity.label for entity in entities] == ["person", "phone"]
    assert parse_keys_yaml(keys.to_yaml()) == {
        "Физлица": ["Иван Петров"],
        "Номера телефонов": ["+7 900 123-45-67"],
    }


def test_keys_omit_empty_sections() -> None:
    _masked, _entities, keys = mask_with(RuleBasedMasker(), "ivan@mail.ru")
    assert list(parse_keys_yaml(keys.to_yaml())) == ["Email"]


def test_keys_yaml_is_empty_when_nothing_masked() -> None:
    _masked, _entities, keys = mask_with(RuleBasedMasker(), "Текст без данных.")
    assert keys.to_yaml() == ""
    assert parse_keys_yaml("") == {}


def test_entity_offsets_point_at_original_text() -> None:
    text = "Контакт: Иван Петров"
    entities = RuleBasedMasker().detect(text)
    assert len(entities) == 1
    entity = entities[0]
    assert text[entity.start : entity.end] == "Иван Петров"


async def test_post_mask_endpoint() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/mask", json={"text": "Иван Петров, ivan@mail.ru"})

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "[PERSON_1], [EMAIL_1]"
    # The response carries aggregates only - raw entity values never leave the API.
    assert body["stats"] == {
        "total": 2,
        "occurrences_by_label": {"person": 1, "email": 1},
        "unique_by_label": {"person": 1, "email": 1},
    }
    assert "masked_entities" not in body
    assert parse_keys_yaml(body["keys"]) == {
        "Физлица": ["Иван Петров"],
        "Email": ["ivan@mail.ru"],
    }


async def test_post_mask_endpoint_cache_hit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§4.6-1: repeated identical sync input is served from the cache."""
    from src.config import Settings
    from src.jobs import dependencies
    from src.jobs.cache import CacheArtifactStore, ResultCache, build_cache_key, fingerprint_for
    from src.masking import router

    cfg = Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://dm:dm@localhost:5432/dm",
            "jobs_dir": str(tmp_path),
            "cache_enabled": True,
            "cache_key_secret": "endpoint-cache-test-secret-32ch",
            "valkey_url": "redis://127.0.0.1:1/0",
            "cache_connect_timeout_seconds": 0.1,
            "cache_socket_timeout_seconds": 0.1,
        }
    )
    cache = ResultCache(cfg, CacheArtifactStore(tmp_path))
    cache._client = _EndpointFakeValkey()
    monkeypatch.setattr(router, "result_cache", lambda: cache)
    monkeypatch.setattr(dependencies, "result_cache", lambda: cache)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/mask", json={"text": "Иван Петров"})
        second = await client.post("/api/mask", json={"text": "Иван Петров"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert cache.metrics["hits"] == 1
    key = build_cache_key(cfg, fingerprint_for("mask_text", input_bytes="Иван Петров".encode()))
    assert key.startswith("docmasker:result:v1:mask_text:")


class _EndpointFakeValkey:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


async def test_post_mask_endpoint_rejects_blank_text() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/mask", json={"text": "   "})

    assert response.status_code == 422
    assert response.json()["detail"] == "Текст пуст."


def test_entity_is_frozen_dataclass() -> None:
    entity = Entity(label="person", text="Иван", start=0, end=4)
    assert entity.score == 1.0
