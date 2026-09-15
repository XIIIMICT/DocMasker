"""
Deterministic, rule-based PII masking for Russian text.

This replaces the former zero-shot GLiNER/PyTorch pipeline. The approach is a
hybrid of morphological extraction (`natasha`), a phone-number library
(`phonenumbers`) and targeted regular expressions. It is fully deterministic,
needs no model download and no GPU, and starts instantly.

Pipeline order (emails → phones → persons → addresses → organisations) is
intentional: patterns are detected independently, then all spans are merged
into a single non-overlapping set (longest/earliest wins) and the text is
rewritten once, back-to-front, so replacements never shift the offsets of
spans still to be applied.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import phonenumbers
from natasha import AddrExtractor, MorphVocab, NamesExtractor

from src.config import get_settings

from .constants import DEFAULT_LABELS

# --- Label vocabulary (mask markers are the upper-cased label) ---------------
PERSON = "person"
PHONE = "phone"
EMAIL = "email"
ADDRESS = "address"
ORGANIZATION = "organization"

#: Entities detected by this pipeline, in the order they are reported.
DETECTABLE_LABELS: tuple[str, ...] = (PERSON, PHONE, EMAIL, ADDRESS, ORGANIZATION)

# --- Regular expressions -----------------------------------------------------
# Email: standard local part plus subdomain chains ("a.b+c@mail.corp.ru").
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+",
)

# Legal-form prefixes for organisations (Cyrillic). The name that follows is
# either a quoted name (straight/guillemet/low-high/English-curly quotes) or a
# capitalised word chain (ООО Ромашка; ПАО Сбербанк). Separators between the
# prefix and the name may include punctuation ("АО, «Росхим»", "АО - «…»").
_ORG_PREFIX = r"ООО|ОАО|ЗАО|ПАО|АО|ИП|ФГБУ|ГБУ|МБУ|ФГУП|ГУП|МУП|НКО|ОО"
_ORG_NAME = r"[А-ЯЁ][\w\-]*(?:\s+[А-ЯЁ][\w\-]*){0,4}"
# Paired quotes accepted around an organisation name: "…", «…», „…“, “…”,
# ‘…’ and the ASCII angle form <…>.
_ORG_QUOTED = (
    r"\"[^\"\n]{1,80}\""
    r"|«[^»\n]{1,80}»"
    r"|„[^“\n]{1,80}“"
    r"|“[^”\n]{1,80}”"
    r"|‘[^’\n]{1,80}’"
    r"|'[^'\n]{1,80}'"
    r"|<[^>\n]{1,80}>"
)
_ORG_PREFIX_RE = re.compile(rf"\b(?:{_ORG_PREFIX})\b\.?[\s,;\-–—]*(?:{_ORG_QUOTED}|{_ORG_NAME})")

# Phrases that introduce an organisation without a legal-form prefix.
_ORG_LEAD_RE = re.compile(
    rf"\b(?:организация|компания|фирма|предприятие|учреждение)\s+(?:{_ORG_QUOTED}|{_ORG_NAME})",
)

# A Cyrillic-capable word (used to trim address spans to sane boundaries).
_WORD_CHAR = re.compile(r"[A-Za-zА-Яа-яЁё0-9]")

#: Minimum a bare `natasha` address part must contribute before we keep it.
_MIN_ADDRESS_PART = 3

# Characters that may not begin/end a merged address span (sentence punctuation,
# stray separators left over from address-part boundaries).
_TRIM_CHARS = " \t\n,.;:-"


@dataclass(frozen=True)
class Entity:
    """A detected span in the *original* text (offsets are absolute)."""

    label: str
    text: str
    start: int
    end: int
    score: float = 1.0


class Span(Protocol):
    """Structural view of a `natasha` match (its `Record` hides these from typing)."""

    start: int
    stop: int


def _spans_overlap(a: Entity, b: Entity) -> bool:
    return a.start < b.end and b.start < a.end


def _merge_spans(entities: Iterable[Entity]) -> list[Entity]:
    """
    Collapse raw detections into a non-overlapping, sorted set.

    Rule: when two spans overlap, the earlier-longer span wins; a span fully
    contained in another is dropped. Ties on position keep the longer span.
    """
    ordered = sorted(entities, key=lambda entity: (entity.start, -(entity.end - entity.start)))
    result: list[Entity] = []
    for entity in ordered:
        if any(_spans_overlap(entity, kept) for kept in result):
            continue
        result.append(entity)
    return sorted(result, key=lambda entity: entity.start)


class RuleBasedMasker:
    """
    Deterministic Russian PII masker.

    Construct once per process (natasha extractors build an in-memory
    morphological dictionary and are expensive to create) and reuse; `mask` is
    stateless and thread-safe for read-only use.
    """

    def __init__(self, labels: Iterable[str] | None = None) -> None:
        self.labels = self._resolve_labels(labels)
        self._morph = MorphVocab()
        self._names = NamesExtractor(self._morph)
        self._addresses = AddrExtractor(self._morph)

    @staticmethod
    def _resolve_labels(configured: Iterable[str] | None) -> tuple[str, ...]:
        if not configured:
            return DEFAULT_LABELS
        resolved = tuple(label.strip() for label in configured if label.strip())
        return resolved or DEFAULT_LABELS

    # -- span providers -------------------------------------------------------

    def _find_emails(self, text: str) -> Iterator[Entity]:
        if EMAIL not in self.labels:
            return
        for match in _EMAIL_RE.finditer(text):
            yield Entity(EMAIL, match.group(0), match.start(), match.end())

    def _find_phones(self, text: str) -> Iterator[Entity]:
        if PHONE not in self.labels:
            return
        for match in phonenumbers.PhoneNumberMatcher(text, "RU"):
            yield Entity(PHONE, match.raw_string, match.start, match.end)

    def _find_persons(self, text: str) -> Iterator[Entity]:
        if PERSON not in self.labels:
            return
        for match in self._names(text):
            fact = match.fact
            # `NamesExtractor` also fires on lone tokens (prepositions, single
            # words, or a bare surname). Require a given name plus a surname or
            # patronymic so only real full names are masked.
            if not fact.first or not (fact.last or fact.middle):
                continue
            yield Entity(PERSON, text[match.start : match.stop], match.start, match.stop)

    def _find_addresses(self, text: str) -> Iterator[Entity]:
        if ADDRESS not in self.labels:
            return
        yield from _merge_address_parts(self._addresses(text), text)

    def _find_organizations(self, text: str) -> Iterator[Entity]:
        if ORGANIZATION not in self.labels:
            return
        for regex in (_ORG_PREFIX_RE, _ORG_LEAD_RE):
            for match in regex.finditer(text):
                start, end = _trim_span(text, match.start(), match.end())
                if end > start:
                    yield Entity(ORGANIZATION, text[start:end], start, end)

    # -- public API -----------------------------------------------------------

    def detect(self, text: str) -> list[Entity]:
        """Return non-overlapping entities found in `text`, sorted by position."""
        raw: list[Entity] = []
        raw.extend(self._find_emails(text))
        raw.extend(self._find_phones(text))
        raw.extend(self._find_persons(text))
        raw.extend(self._find_addresses(text))
        raw.extend(self._find_organizations(text))
        return _merge_spans(raw)

    def mask(self, text: str, entities: list[Entity] | None = None) -> str:
        """Replace detected spans with `[LABEL]`, working back-to-front."""
        if entities is None:
            entities = self.detect(text)
        masked = text
        for entity in sorted(entities, key=lambda item: item.start, reverse=True):
            masked = masked[: entity.start] + f"[{entity.label.upper()}]" + masked[entity.end :]
        return masked


def _merge_address_parts(parts: Iterable[Span], text: str) -> Iterator[Entity]:
    """
    Combine `natasha` ``AddrPart`` matches into whole address spans.

    `AddrExtractor` yields one match per component ("Москва", "Ленина", "5"),
    so consecutive parts separated only by whitespace/punctuation are glued
    into a single ``[ADDRESS]`` span. Parts too short to be meaningful are
    skipped, and isolated city/street-only matches are still kept when they
    carry an explicit address keyword (``г.``, ``ул.``).
    """
    matches = sorted(parts, key=lambda part: part.start)
    for start, stop in _group_address_ranges(matches):
        trimmed_start, trimmed_stop = _trim_span(text, start, stop)
        if trimmed_stop - trimmed_start < _MIN_ADDRESS_PART:
            continue
        raw = text[trimmed_start:trimmed_stop]
        if not _looks_like_address(raw):
            continue
        yield Entity(ADDRESS, raw, trimmed_start, trimmed_stop)


def _group_address_ranges(matches: list[Span]) -> Iterator[tuple[int, int]]:
    """Group adjacent address parts (gap = only separators) into char ranges."""
    current_start: int | None = None
    current_stop = -1
    for match in matches:
        start = int(match.start)
        stop = int(match.stop)
        if current_start is None:
            current_start, current_stop = start, stop
            continue
        gap = start - current_stop
        if 0 <= gap <= 3:
            current_stop = stop
        else:
            yield current_start, current_stop
            current_start, current_stop = start, stop
    if current_start is not None:
        yield current_start, current_stop


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in _TRIM_CHARS:
        start += 1
    while end > start and text[end - 1] in _TRIM_CHARS:
        end -= 1
    return start, end


def _looks_like_address(raw: str) -> bool:
    """Reject stubs that are just a stray word or a lone number."""
    if not _WORD_CHAR.search(raw):
        return False
    has_keyword = any(
        marker in raw.lower() for marker in ("г.", "г ", "ул", "д.", "д ", "просп", "пер", "шоссе", "обл", "р-н", "кв")
    )
    has_digits = any(char.isdigit() for char in raw)
    return has_keyword or has_digits


@lru_cache(maxsize=1)
def get_masker() -> RuleBasedMasker:
    """
    Process-wide masker singleton.

    Building the natasha extractors loads the morphological dictionary into
    memory (``MorphVocab``), which is why this must be cached rather than
    re-created per request.
    """
    raw = get_settings().mask_labels
    labels = raw.split("|") if raw else None
    return RuleBasedMasker(labels)
