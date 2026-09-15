"""
Default set of sensitive entity labels for PII masking.

Labels are the canonical English names used throughout the deterministic
pipeline (see `rule_based.py`); the mask marker is the upper-cased label
(`[PERSON]`, `[PHONE]`, …). Users can override the active set via `MASK_LABELS`
(pipe-separated).
"""

DEFAULT_LABELS: tuple[str, ...] = (
    "person",
    "phone",
    "email",
    "address",
    "organization",
)

#: Organisation names masked unconditionally — case-insensitively and with or
#: without a legal-form prefix. Project-specific exclusions (brands that must
#: never leak); applied as a last resort after the rule-based spans are merged.
ALWAYS_MASK_ORGANIZATIONS: tuple[str, ...] = ("Росхим",)


def mask_marker(label: str) -> str:
    """Marker text for an entity label (`person` -> ``[PERSON]``)."""
    return f"[{label.upper()}]"
