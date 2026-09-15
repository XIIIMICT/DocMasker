"""
Text masking service.

Thin orchestration over `RuleBasedMasker`: detect spans in a request and
render the masked text. The masking itself is deterministic rule-based logic
(natasha + phonenumbers + regex), so there is no model, no warm-up and no
network access involved.
"""

from __future__ import annotations

from .constants import mask_marker
from .rule_based import Entity, RuleBasedMasker, get_masker
from .schemas import MaskedEntity, MaskRequest


def mask_text(text: str, entities: list[MaskedEntity]) -> str:
    """
    Replace each detected span with ``[LABEL]``, working back-to-front.

    Positions refer to the original text; replacing in reverse keeps earlier
    offsets valid.
    """
    masked = text
    for entity in sorted(entities, key=lambda item: item.start, reverse=True):
        masked = masked[: entity.start] + mask_marker(entity.label) + masked[entity.end :]
    return masked


def to_schema(entity: Entity) -> MaskedEntity:
    """Convert an internal `Entity` into the API schema."""
    return MaskedEntity(
        label=entity.label,
        text=entity.text,
        start=entity.start,
        end=entity.end,
        score=entity.score,
    )


def mask_with(masker: RuleBasedMasker, text: str) -> tuple[str, list[MaskedEntity]]:
    """Detect and mask `text` with an explicit masker (used by tests/docx)."""
    entities = [to_schema(entity) for entity in masker.detect(text)]
    return mask_text(text, entities), entities


def mask_document(request: MaskRequest) -> tuple[str, list[MaskedEntity]]:
    """Run the full pipeline for a `/api/mask` request using the singleton masker."""
    return mask_with(get_masker(), request.text)
