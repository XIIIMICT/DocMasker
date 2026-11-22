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

from collections.abc import Mapping

from .mapping import MaskKeys, placeholders_from_document, validate_keys_yaml
from .rule_based import Entity, RuleBasedMasker, get_masker
from .schemas import MaskedEntity, MaskRequest, UnmaskRequest


def mask_text(text: str, entities: list[MaskedEntity], keys: MaskKeys) -> str:
    """
    Replace each detected span with ``[LABEL_N]``, working back-to-front.

    Positions refer to the original text; replacing in reverse keeps earlier
    offsets valid. Numbers are allocated left-to-right first, so they follow
    document order regardless of the rewrite order.
    """
    placeholders = {
        (entity.start, entity.end): keys.placeholder(entity.label, entity.text)
        for entity in sorted(entities, key=lambda item: item.start)
    }
    masked = text
    for entity in sorted(entities, key=lambda item: item.start, reverse=True):
        marker = placeholders[(entity.start, entity.end)]
        masked = masked[: entity.start] + marker + masked[entity.end :]
    return masked


def unmask_text(text: str, placeholders: Mapping[str, str]) -> str:
    """
    Replace every known placeholder back with its original value.

    A plain left-to-right substitution: placeholders are self-delimiting
    (``[LABEL_N]``), so no ordering or back-to-front dance is needed. A
    placeholder missing from the keys is left in place rather than dropped.
    """
    restored = text
    for placeholder, value in placeholders.items():
        restored = restored.replace(placeholder, value)
    return restored


def to_schema(entity: Entity) -> MaskedEntity:
    """Convert an internal `Entity` into the API schema."""
    return MaskedEntity(
        label=entity.label,
        text=entity.text,
        start=entity.start,
        end=entity.end,
        score=entity.score,
    )


def mask_with(masker: RuleBasedMasker, text: str) -> tuple[str, list[MaskedEntity], MaskKeys]:
    """Detect and mask `text` with an explicit masker (used by tests/docx)."""
    entities = [to_schema(entity) for entity in masker.detect(text)]
    keys = MaskKeys()
    return mask_text(text, entities, keys), entities, keys


def mask_document(request: MaskRequest) -> tuple[str, list[MaskedEntity], MaskKeys]:
    """Run the full pipeline for a `/api/mask` request using the singleton masker."""
    return mask_with(get_masker(), request.text)


def unmask_document(request: UnmaskRequest) -> str:
    """Restore a masked text for a `/api/unmask` request from its keys."""
    placeholders = placeholders_from_document(validate_keys_yaml(request.keys))
    return unmask_text(request.text, placeholders)
