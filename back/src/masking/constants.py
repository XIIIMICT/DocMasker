"""
Default set of sensitive entity labels for PII masking.

Labels are passed to GLiNER verbatim (lower-case English zero-shot prompts).
Keep them simple and aligned with what `gliner_multi_pii-v1` understands;
users can override via GLINER_LABELS (pipe-separated).
"""

DEFAULT_GLINER_LABELS: list[str] = [
    "person",
    "phone",
    "email",
    "address",
    "organization",
    "location",
    "passport",
    "inn",
    "snils",
    "date of birth",
]
