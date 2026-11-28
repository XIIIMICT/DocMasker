"""
Exception matching and cheap detector preconditions for the masking pipeline.

`always_mask` / `never_mask` are compiled from validated literal values only —
configuration never supplies executable regex. Every matcher is word-boundary
guarded, and the preconditions below are deliberately weak: a false "run" only
costs time, a false "skip" would silently lose PII.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .detection_helpers import Entity, _address_candidate_re, _normalize

if TYPE_CHECKING:
    from .config import ExceptionValue


@dataclass(frozen=True, slots=True)
class _ExceptionPattern:
    """Word-boundary-guarded literal matchers for one exception group."""

    patterns: tuple[re.Pattern[str], ...]

    def finditer(self, text: str) -> Iterator[re.Match[str]]:
        for pattern in self.patterns:
            yield from pattern.finditer(text)


_BOUNDARY_LEFT = r"(?<![\wА-Яа-яЁё])"
_BOUNDARY_RIGHT = r"(?![\wА-Яа-яЁё])"


def _compile_exception_literals(
    literals: Iterable[str],
    *,
    ignore_case: bool,
    token_sequence: bool,
) -> re.Pattern[str] | None:
    parts: set[str] = set()
    for literal in literals:
        if token_sequence:
            parts.add(r"\s+".join(re.escape(token) for token in _normalize(literal).split()))
        else:
            parts.add(re.escape(_normalize(literal)))
    if not parts:
        return None
    body = "|".join(sorted(parts, key=len, reverse=True))
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(f"{_BOUNDARY_LEFT}(?:{body}){_BOUNDARY_RIGHT}", flags)


def _exception_pattern(values: tuple[ExceptionValue, ...]) -> _ExceptionPattern:
    """
    Compile each declared match mode with its documented semantics.

    `exact` is a case-sensitive literal, `case_insensitive` a case-insensitive
    literal and `token_sequence` an ordered token sequence with arbitrary
    whitespace between tokens (also case-insensitive). All are NFC-normalised,
    word-boundary guarded and built from literals only — never from regex data
    supplied by configuration.
    """
    by_mode: dict[str, list[str]] = {}
    for value in values:
        by_mode.setdefault(value.match, []).extend((value.value, *value.variants))
    compiled = (
        _compile_exception_literals(
            by_mode[mode],
            ignore_case=mode != "exact",
            token_sequence=mode == "token_sequence",
        )
        for mode in sorted(by_mode)
    )
    return _ExceptionPattern(tuple(pattern for pattern in compiled if pattern is not None))


def _suppressed(entity: Entity, patterns: dict[str, _ExceptionPattern]) -> bool:
    """
    Suppress only when the configured value covers the whole detected span.

    `never_mask` entries never suppress a larger unrelated span merely because
    they overlap one token.
    """
    pattern = patterns.get(entity.label)
    if pattern is None:
        return False
    normalized = _normalize(entity.text)
    return any(match.group(0).casefold() == normalized.casefold() for match in pattern.finditer(normalized))


#: Conservative cheap preconditions (Phase 7 §9.2). A detector is skipped only
#: when its input cannot possibly contain a matching pattern. `always_mask`
#: values bypass this entirely (they run over the full text afterwards), and
#: `never_mask` suppression is unaffected. Keep these WEAK: a false "skip"
#: would silently lose PII, a false "run" only costs time.
_DETECTOR_PRECONDITIONS: dict[str, Callable[[str], bool]] = {
    "email": lambda text: "@" in text,
    "link": lambda text: "://" in text,
    "phone": lambda text: any(char.isdigit() for char in text),
    "person": lambda text: any(char.isupper() for char in text),
    # Use the same validated, anchored marker matcher as the expensive address
    # extractor; one-letter abbreviations never match loose substrings.
    "address": lambda text: any(char.isdigit() for char in text) or bool(_address_candidate_re().search(text)),
    "organization": lambda text: any(char.isupper() for char in text),
}


def _cheap_prefilter(text: str) -> frozenset[str]:
    """Detector ids whose cheap precondition passes for this short text."""
    return frozenset(detector_id for detector_id, allows in _DETECTOR_PRECONDITIONS.items() if allows(text))
