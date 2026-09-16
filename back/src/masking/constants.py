"""
Default set of sensitive entity labels for PII masking.

Labels are the canonical English names used throughout the deterministic
pipeline (see `rule_based.py`); the mask marker is the upper-cased label with a
per-type sequence number (`[PERSON_1]`, `[PHONE_2]`, …). Users can override the
active set via `MASK_LABELS` (pipe-separated).
"""

DEFAULT_LABELS: tuple[str, ...] = (
    "person",
    "phone",
    "email",
    "address",
    "organization",
)

#: Russian section titles for the demasking keys file, in the order the sections
#: are written. Only labels present in `DEFAULT_LABELS` are used.
LABEL_TITLES: dict[str, str] = {
    "person": "Физлица",
    "phone": "Номера телефонов",
    "email": "Email",
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
