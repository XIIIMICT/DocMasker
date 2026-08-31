"""Tests for the masking service (pure logic) and the /api/mask endpoint.

The real GLiNER model is never loaded: a deterministic stub implements the
`GLiNERModelLike` interface so windowing, detection and redaction are tested
fast and without network access.
"""

from __future__ import annotations

import httpx

from src.masking import service as masking_service
from src.masking.router import router as masking_router
from src.masking.schemas import MaskedEntity, MaskRequest
from src.masking.service import (
    detect_entities,
    mask_text,
)


class _Config:
    def __init__(self, max_len: int) -> None:
        self.max_len = max_len


class StubModel:
    """Deterministic GLiNER stand-in: matches a fixed substring per label."""

    def __init__(self, labels: list[str], max_len: int = 64) -> None:
        self.labels = list(labels)
        self.threshold = 0.99
        self.config = _Config(max_len)

    def prepare_batch(self, text: str, labels: list[str]) -> dict:
        # One token per character keeps token==char indices simple.
        return {
            "tokens": [list(text)],
            "start_token_map": [list(range(len(text)))],
            "end_token_map": [list(range(1, len(text) + 1))],
        }

    def predict_entities(self, text: str, labels: list[str], threshold: float = 0.5) -> list[dict]:
        out: list[dict] = []
        needle = "2024/01/01"
        idx = 0
        while True:
            idx = text.find(needle, idx)
            if idx == -1:
                break
            out.append({"text": needle, "label": "date of birth", "score": 0.9, "start": idx, "end": idx + len(needle)})
            idx += len(needle)
        return out


def test_mask_text_replaces_spans() -> None:
    entities = [
        MaskedEntity(label="person", text="Иван", start=4, end=8, score=0.9),
        MaskedEntity(label="phone", text="+7 900", start=14, end=20, score=0.9),
    ]
    result = mask_text("Кто Иван, тел +7 900.", entities)
    assert result == "Кто [PERSON], тел [PHONE]."


def test_detect_entities_crosses_window_boundary() -> None:
    # A short window (max_len=8) forces a second window for the trailing text.
    model = StubModel(labels=["date of birth"], max_len=10)
    text = "aaa 2024/01/01 bbb 2024/01/01"
    entities = detect_entities(model, text, labels=["date of birth"], threshold=0.5)
    assert len(entities) == 2
    assert all(entity.text == "2024/01/01" for entity in entities)
    # Deduplicated: no overlapping spans with the same label for the same range.
    keys = {(entity.start, entity.end, entity.label) for entity in entities}
    assert len(keys) == 2


def test_mask_document_end_to_end() -> None:
    model = StubModel(labels=["date of birth"])
    request = MaskRequest(text="Род. 2024/01/01, остальное не содержит.")
    masked, entities = masking_service.mask_document(model, request)
    assert masked == "Род. [DATE OF BIRTH], остальное не содержит."
    assert entities[0].label == "date of birth"


def test_window_split_multi_hash() -> None:
    # Fits in one window -> single window covering the whole text.
    model = StubModel(labels=["date of birth"], max_len=100)
    windows = masking_service.iter_windows(model, "short", ["date of birth"])
    assert windows == [(0, 5, "short")]


def test_empty_lookup_uses_defaults() -> None:
    assert masking_service.resolve_labels(None) == masking_service.DEFAULT_GLINER_LABELS


async def test_post_mask_endpoint_transport() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(masking_router)
    app.dependency_overrides[masking_service.get_gliner_model] = lambda: StubModel(labels=["date of birth"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mask", json={"text": "x 2024/01/01"})

    assert response.status_code == 200
    assert response.json()["text"] == "x [DATE OF BIRTH]"
