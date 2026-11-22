"""
Base data for the masking pipeline.

The default label set and the project-specific always-masked organizations.
Structural metadata (titles, display order, profiles) lives in `profiles.py`;
detection lives in `rule_based.py`.
"""

#: The canonical label ids of the default profile (see `profiles.py`).
DEFAULT_LABELS: tuple[str, ...] = (
    "person",
    "phone",
    "email",
    "link",
    "address",
    "organization",
)

#: Organization names masked unconditionally — case-insensitively and with or
#: without a legal-form prefix. Project-specific exclusions (brands that must
#: never leak); consumed by the default profile's `always_mask_organizations`.
ALWAYS_MASK_ORGANIZATIONS: tuple[str, ...] = ("Росхим",)


def mask_marker(label: str, index: int) -> str:
    """Marker text for an entity label and its 1-based number (`[PERSON_1]`)."""
    return f"[{label.upper()}_{index}]"
