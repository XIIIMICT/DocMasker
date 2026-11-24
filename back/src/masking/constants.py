"""
Minimal structural helpers for the masking pipeline (Phase 4).

Curated vocabulary, label titles and exceptions now live in validated data
files (`config/masking/`, see `masking/config.py`). Only pure functions remain
here.
"""


def mask_marker(label: str, index: int) -> str:
    """Marker text for an entity label and its 1-based number (`[PERSON_1]`)."""
    return f"[{label.upper()}_{index}]"
