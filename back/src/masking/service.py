"""
GLiNER-based masking service.

The heavy GLiNER model is loaded lazily once per process (see
`get_gliner_model`) and reused across requests. Long documents are split
into overlapping windows aligned to the model's word-splitter tokenisation
(`prepare_batch`), so spans crossing the `config.max_len` boundary are not
silently dropped (GLiNER truncates by default). Predictions from overlapping
windows are deduplicated by (start, end, label), keeping the highest score.
"""

from __future__ import annotations

import logging
from typing import Protocol, cast

from gliner import GLiNER

from src.config import get_settings

from .constants import DEFAULT_GLINER_LABELS
from .schemas import MaskedEntity, MaskRequest

LOGGER = logging.getLogger("docmasker.masking")

# Characters added to the model's window budget as a fallback when we cannot
# rely on `prepare_batch` (must be >> the token-to-char expansion factor).
_WINDOW_CHAR_ESTIMATE = 4096
_DEFAULT_OVERLAP_TOKENS = 32


class GLiNERModelLike(Protocol):
    """The subset of the GLiNER model interface the service uses."""

    labels: list[str]
    threshold: float

    @property
    def config(self) -> object: ...

    def predict_entities(self, text: str, labels: list[str], threshold: float = 0.5) -> list[dict]: ...

    def prepare_batch(self, text: str, labels: list[str]) -> dict: ...


def build_model(model_id: str, labels: list[str], threshold: float) -> GLiNERModelLike:
    """
    Load a GLiNER model configured for this instance.

    Labels/threshold are bound via a thin closure so the router and tests can
    inject a light stub implementing `GLiNERModelLike`.
    """
    # `GLiNER` satisfies GLiNERModelLike structurally, but `from_pretrained`
    # (inherited from PyTorchModelHubMixin) has a broken return annotation
    # (Tensor | Module) — cast it to the protocol we actually use.
    model = cast("GLiNERModelLike", GLiNER.from_pretrained(model_id))
    return _ConfiguredModel(model, labels, threshold)


class _ConfiguredModel:
    """Binds the active label set and threshold into a callable-shaped model."""

    def __init__(self, model: GLiNERModelLike, labels: list[str], threshold: float) -> None:
        self._model = model
        self.labels = list(labels)
        self.threshold = threshold

    @property
    def config(self) -> object:
        return self._model.config

    def predict_entities(self, text: str, labels: list[str], threshold: float = 0.5) -> list[dict]:
        return list(self._model.predict_entities(text, labels, threshold=threshold))

    def prepare_batch(self, text: str, labels: list[str]) -> dict:
        return self._model.prepare_batch(text, labels)


def resolve_labels(configured: list[str] | None) -> list[str]:
    """Use the configured labels, stripping empties, or fall back to defaults."""
    if not configured:
        return list(DEFAULT_GLINER_LABELS)
    return [label.strip() for label in configured if label.strip()]


def iter_windows(
    model: GLiNERModelLike,
    text: str,
    labels: list[str],
) -> list[tuple[int, int, str]]:
    """
    Split `text` into overlapping windows aligned to the model tokeniser.

    Returns a list of (char_start, char_end, window_text) in document order.
    A single window is returned when the text fits within `config.max_len`.
    """
    max_len = int(getattr(model.config, "max_len", _WINDOW_CHAR_ESTIMATE))
    overlap = min(_DEFAULT_OVERLAP_TOKENS, max(0, max_len - 1)) if max_len > 1 else 0

    try:
        prepared = model.prepare_batch(text, labels)
    except Exception:  # pragma: no cover - degraded fallback
        prepared = None

    if prepared is None or not prepared.get("tokens"):
        return _split_fallback(text, max_len, overlap)

    tokens = prepared["tokens"][0]
    starts = prepared["start_token_map"][0]
    ends = prepared["end_token_map"][0]
    if len(tokens) <= max_len:
        return [(0, len(text), text)]

    step = max_len - overlap
    windows: list[tuple[int, int, str]] = []
    for first in range(0, len(tokens), step):
        last = min(first + max_len, len(tokens))
        char_start = int(starts[first])
        char_end = int(ends[last - 1])
        windows.append((char_start, char_end, text[char_start:char_end]))
        if last == len(tokens):
            break
    return windows


def _split_fallback(text: str, max_chars: int, overlap: int) -> list[tuple[int, int, str]]:
    """Character-budget windowing used if the model exposes no token maps."""
    if len(text) <= max_chars:
        return [(0, len(text), text)]
    step = max_chars - overlap
    windows: list[tuple[int, int, str]] = []
    for first in range(0, len(text), step):
        last = min(first + max_chars, len(text))
        windows.append((first, last, text[first:last]))
        if last == len(text):
            break
    return windows


def detect_entities(
    model: GLiNERModelLike,
    text: str,
    labels: list[str] | None = None,
    threshold: float | None = None,
) -> list[MaskedEntity]:
    """Detect sensitive spans across the whole document (windowed)."""
    if labels is None:
        labels = list(model.labels) if model.labels else resolve_labels(None)
    else:
        labels = resolve_labels(labels)
    threshold = threshold if threshold is not None else model.threshold
    by_key: dict[tuple[int, int, str], MaskedEntity] = {}

    for char_start, _char_end, window_text in iter_windows(model, text, labels):
        for raw in model.predict_entities(window_text, labels, threshold=threshold):
            rel_start = int(raw["start"])
            rel_end = int(raw["end"])
            abs_start = char_start + rel_start
            abs_end = char_start + rel_end
            label = str(raw["label"])
            score = float(raw["score"])
            key = (abs_start, abs_end, label)
            existing = by_key.get(key)
            if existing is None or score > existing.score:
                by_key[key] = MaskedEntity(
                    label=label,
                    text=text[abs_start:abs_end],
                    start=abs_start,
                    end=abs_end,
                    score=score,
                )

    return sorted(by_key.values(), key=lambda entity: (entity.start, entity.end))


def mask_text(text: str, entities: list[MaskedEntity]) -> str:
    """
    Replace each detected span with [LABEL], working back-to-front.

    Positions refer to the original text; replacing in reverse keeps earlier
    offsets valid.
    """
    masked = text
    for entity in sorted(entities, key=lambda entity: entity.start, reverse=True):
        masked = masked[: entity.start] + f"[{entity.label.upper()}]" + masked[entity.end :]
    return masked


def mask_document(model: GLiNERModelLike, request: MaskRequest) -> tuple[str, list[MaskedEntity]]:
    """Run the full pipeline: detect entities and build the masked text."""
    entities = detect_entities(model, request.text)
    masked = mask_text(request.text, entities)
    return masked, entities


_model_holder: dict[str, GLiNERModelLike | None] = {"instance": None}
load_model = build_model


def get_gliner_model() -> GLiNERModelLike:
    """
    Return the process-wide GLiNER singleton, loading it on first use.

    Tests may swap `load_model` (or clear the holder via `reset_model`) to
    avoid downloading the real model.
    """
    if _model_holder["instance"] is None:
        settings = get_settings()
        labels = _parse_configured_labels(settings.gliner_labels)
        LOGGER.warning("Loading GLiNER model %s (first inference may be slow)...", settings.gliner_model)
        _model_holder["instance"] = load_model(settings.gliner_model, labels, settings.gliner_threshold)
        LOGGER.warning("GLiNER model %s loaded.", settings.gliner_model)
    return _model_holder["instance"]  # type: ignore[return-value]


def _parse_configured_labels(raw: str | None) -> list[str]:
    """Turn the pipe-separated GLINER_LABELS value into a list (defaults if empty)."""
    if not raw:
        return list(DEFAULT_GLINER_LABELS)
    return [part.strip() for part in raw.split("|") if part.strip()]


def reset_model() -> None:
    """Drop the cached model (used by tests to force a reload)."""
    _model_holder["instance"] = None
