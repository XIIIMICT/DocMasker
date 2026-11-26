"""
Text masking and unmasking service.

Thin orchestration over `RuleBasedMasker`: detect spans in a request and
render the masked text with a per-entity placeholder. The masking itself is
deterministic rule-based logic (natasha + phonenumbers + regex), so there is no
model, no warm-up and no network access involved.

Masking is reversible: each occurrence becomes a numbered ``[LABEL_N]`` marker
and its original value is collected in a `MaskKeys` document (see `mapping.py`).
Unmasking is the inverse: replace every known placeholder with its original
value.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Protocol

from fastapi import status

from src.errors import ApiError

from .mapping import (
    MaskKeys,
    compile_placeholders,
    ensure_no_marker_collision,
    placeholders_from_document,
    validate_keys_yaml,
)
from .rule_based import Entity, RuleBasedMasker, get_masker
from .schemas import MaskedEntity, MaskRequest, MaskStats, UnmaskRequest

#: Fixed Russian details for invalid spans — no offsets or entity values leak.
_INVALID_SPAN_DETAILS = {
    "type": "Позиции сущностей должны быть целыми числами.",
    "bounds": "Позиции сущностей выходят за пределы текста.",
    "empty": "Обнаружены пустые или обратные области сущностей.",
    "overlap": "Обнаружены перекрывающиеся сущности.",
    "mismatch": "Значение сущности не совпадает с фрагментом исходного текста.",
}


class InvalidSpanError(ApiError):
    """Detected spans cannot be applied to the source without corrupting it."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("invalid_span", _INVALID_SPAN_DETAILS[reason], status.HTTP_422_UNPROCESSABLE_CONTENT)


def _validated_spans(text: str, entities: Sequence[MaskedEntity]) -> list[MaskedEntity]:
    """
    Order entities by source offset and reject spans that cannot be applied.

    Adjacent spans (``end == next.start``) are allowed; overlaps, empty or
    reversed ranges, out-of-range offsets and values that disagree with their
    source slice fail closed *before* any placeholder number is allocated.
    """
    if any(
        isinstance(entity.start, bool)
        or isinstance(entity.end, bool)
        or not isinstance(entity.start, int)
        or not isinstance(entity.end, int)
        for entity in entities
    ):
        raise InvalidSpanError("type")

    ordered = sorted(entities, key=lambda item: (item.start, item.end))
    cursor = 0
    for entity in ordered:
        start, end = entity.start, entity.end
        if start < 0 or end > len(text) or start > end:
            raise InvalidSpanError("bounds")
        if start == end:
            raise InvalidSpanError("empty")
        if start < cursor:
            raise InvalidSpanError("overlap")
        if entity.text != text[start:end]:
            raise InvalidSpanError("mismatch")
        cursor = end
    return ordered


class _SliceSource(Protocol):
    """Read-only slice view required by `_mask_parts` (plain `str` satisfies it)."""

    def __len__(self) -> int: ...

    def __getitem__(self, key: slice, /) -> str: ...


def _mask_parts(text: _SliceSource, spans: Sequence[tuple[int, int, str]]) -> list[str]:
    """Collect disjoint source slices and markers in document order, once."""
    parts: list[str] = []
    cursor = 0
    for start, end, marker in spans:
        if start > cursor:
            parts.append(text[cursor:start])
        parts.append(marker)
        cursor = end
    if cursor < len(text):
        parts.append(text[cursor:])
    return parts


def mask_text(text: str, entities: list[MaskedEntity], keys: MaskKeys) -> str:
    """
    Replace each detected span with ``[LABEL_N]`` in one left-to-right pass.

    Spans are validated and sorted first; numbers are then allocated in
    document order, so identical values still share one key and distinct
    values keep their own number. Untouched slices and markers are appended
    to one list and joined once — no intermediate full-string copies.
    """
    ordered = _validated_spans(text, entities)
    spans = [(entity.start, entity.end, keys.placeholder(entity.label, entity.text)) for entity in ordered]
    # Fail before any rewrite: a literal marker in the source that equals one
    # of the markers allocated here would gain a second meaning on restoration.
    ensure_no_marker_collision([text], [marker for _start, _end, marker in spans])
    return "".join(_mask_parts(text, spans))


def unmask_text(text: str, placeholders: Mapping[str, str]) -> str:
    """
    Restore every known placeholder in one non-cascading scan.

    A compiled longest-first alternation replaces each match with its mapped
    value; `re.sub` does not rescan inserted text, so a value that itself
    contains a valid marker stays opaque instead of collapsing further. A
    placeholder missing from the keys is left in place rather than dropped.
    """
    pattern = compile_placeholders(placeholders)
    if pattern is None:
        return text
    return pattern.sub(lambda match: placeholders[match.group(0)], text)


def to_schema(entity: Entity) -> MaskedEntity:
    """Convert an internal `Entity` into the API schema."""
    return MaskedEntity(
        label=entity.label,
        text=entity.text,
        start=entity.start,
        end=entity.end,
        score=entity.score,
    )


def mask_stats(entities: list[MaskedEntity]) -> MaskStats:
    """Aggregate per-label occurrence/unique counts (no raw values over the wire)."""
    occurrences: Counter[str] = Counter(entity.label for entity in entities)
    uniques: dict[str, set[str]] = {}
    for entity in entities:
        uniques.setdefault(entity.label, set()).add(entity.text)
    return MaskStats(
        total=len(entities),
        occurrences_by_label=dict(occurrences),
        unique_by_label={label: len(values) for label, values in uniques.items()},
    )


def mask_with(masker: RuleBasedMasker, text: str) -> tuple[str, list[MaskedEntity], MaskKeys]:
    """Detect and mask `text` with an explicit masker (used by tests/docx)."""
    entities = [to_schema(entity) for entity in masker.detect(text)]
    keys = MaskKeys()
    return mask_text(text, entities, keys), entities, keys


def mask_document(request: MaskRequest) -> tuple[str, list[MaskedEntity], MaskKeys]:
    """Run the full pipeline for a `/api/mask` request (one configuration)."""
    return mask_with(get_masker(), request.text)


def unmask_document(request: UnmaskRequest) -> str:
    """Restore a masked text for a `/api/unmask` request from its keys."""
    placeholders = placeholders_from_document(validate_keys_yaml(request.keys))
    return unmask_text(request.text, placeholders)
