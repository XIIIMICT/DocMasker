"""
Default set of sensitive entity labels for PII masking.

Labels are the canonical English names used throughout the deterministic
pipeline (see `rule_based.py`); the mask marker is the upper-cased label with a
per-type sequence number (`[PERSON_1]`, `[PHONE_2]`, …). Users can override the
active set via `MASK_LABELS` (pipe-separated).
"""

from collections.abc import Iterable


class LabelsValidationError(ValueError):
    """The configured label set is not a valid label list."""


def resolve_labels(configured: Iterable[str] | None) -> tuple[str, ...]:
    """
    Validate and resolve a configured label list against `DEFAULT_LABELS`.

    `None` or an empty input keeps the defaults; blank tokens are skipped.
    Duplicates and unknown labels are configuration errors (a silently accepted
    unknown label would later produce placeholders without keys sections), so
    they raise `LabelsValidationError` with a Russian message — the settings
    validator calls this at startup, `RuleBasedMasker` on direct construction.
    """
    if not configured:
        return DEFAULT_LABELS
    resolved: list[str] = []
    for raw in configured:
        label = raw.strip()
        if not label:
            continue
        if label not in DEFAULT_LABELS:
            known = ", ".join(DEFAULT_LABELS)
            msg = f"Неизвестный тип сущности: «{label}». Допустимые значения: {known}."
            raise LabelsValidationError(msg)
        if label in resolved:
            msg = f"Тип сущности «{label}» указан больше одного раза."
            raise LabelsValidationError(msg)
        resolved.append(label)
    return tuple(resolved) or DEFAULT_LABELS


DEFAULT_LABELS: tuple[str, ...] = (
    "person",
    "phone",
    "email",
    "link",
    "address",
    "organization",
)

#: Russian section titles for the demasking keys file, in the order the sections
#: are written. Only labels present in `DEFAULT_LABELS` are used.
LABEL_TITLES: dict[str, str] = {
    "person": "Физлица",
    "phone": "Номера телефонов",
    "email": "Email",
    "link": "Ссылки",
    "organization": "Организации",
    "address": "Адреса",
}

#: Organisation names masked unconditionally — case-insensitively and with or
#: without a legal-form prefix. Project-specific exclusions (brands that must
#: never leak); applied as a last resort after the rule-based spans are merged.
ALWAYS_MASK_ORGANIZATIONS: tuple[str, ...] = ("Росхим",)


def mask_marker(label: str, index: int) -> str:
    """Marker text for an entity label and its 1-based number (`[PERSON_1]`)."""
    return f"[{label.upper()}_{index}]"
