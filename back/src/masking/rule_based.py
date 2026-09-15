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

import difflib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import phonenumbers
from natasha import AddrExtractor, MorphVocab, NamesExtractor

from src.config import get_settings

from .constants import ALWAYS_MASK_ORGANIZATIONS, DEFAULT_LABELS

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

# Legal-form prefixes for organisations. The name that follows is either a
# quoted name (straight/guillemet/low-high/English-curly quotes) or a capitalised
# word chain (ООО Ромашка; ПАО Сбербанк). Separators between the prefix and the
# name may include punctuation. Spelled-out forms ("Акционерное общество",
# "Общество с ограниченной ответственностью") are matched separately by
# `_match_full_form` in any grammatical case.
_ORG_ABBREV = r"ООО|ОАО|ЗАО|ПАО|АО|ИП|ФГБУ|ГБУ|МБУ|ФГУП|ГУП|МУП|НКО|ОО"

#: Spelled-out legal forms, as flat lemma sequences (nominative phrases lemmatised
#: once here so the matcher never needs a per-word case table). A token in the
#: text matches a lemma when their lemmas are equal *or* similar enough
#: (``_ORG_LEMMA_RATIO``) — that admits any declension ("Акционерным обществом",
#: "Общества с ограниченной ответственностью") and small inflections, without
#: hand-writing cases.
_ORG_FULL_FORMS: tuple[tuple[str, ...], ...] = (
    ("акционерный", "общество"),
    ("публичный", "акционерный", "общество"),
    ("закрытый", "акционерный", "общество"),
    ("открытый", "акционерный", "общество"),
    ("общество", "с", "ограниченный", "ответственность"),
    ("унитарный", "предприятие"),
    ("индивидуальный", "предприниматель"),
)

#: Shortest legal-form phrases we try to match, in words.
_ORG_FORM_LENGTHS = tuple(sorted({len(form) for form in _ORG_FULL_FORMS}))

#: Minimum lemma similarity for a token to count as part of a legal form.
#: "акциноерным" (typo) vs "акционерный" scores 0.909; unrelated words stay
#: well below (max observed false positive: 0.889 for "сообществом").
_ORG_LEMMA_RATIO = 0.9

#: Separators allowed *between* the words of a legal form ("Общество, с ограниченной …").
_ORG_GAP_RE = re.compile(r"[\s,.;:()\-–—]+")

#: A single word token anywhere in the text.
_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]*")

#: Characters allowed between a legal-form prefix and the organisation name.
_ORG_SEPARATORS = " \t\n,;.-–—"

_ORG_NAME = r"[А-ЯЁ][\w\-]*(?:\s+[А-ЯЁ][\w\-]*){0,4}"
# Paired quotes accepted around an organisation name: "…", «…», „…“, “…”, ‘…’
# and the ASCII angle form <…>.
_ORG_QUOTED = (
    r"\"[^\"\n]{1,80}\""
    r"|«[^»\n]{1,80}»"
    r"|„[^“\n]{1,80}“"
    r"|“[^”\n]{1,80}”"
    r"|‘[^’\n]{1,80}’"
    r"|'[^'\n]{1,80}'"
    r"|<[^>\n]{1,80}>"
)
#: The name after a prefix: a quoted name *including its quotes*, or a
#: capitalised word chain. The quotes are kept so they are masked as well.
_ORG_NAME_PART = rf"(?:{_ORG_QUOTED})|{_ORG_NAME}"

#: A capitalised word chain that can act as an organisation name.
_ORG_NAME_RE = re.compile(_ORG_NAME)
#: A quoted organisation name — quotes included, so they are masked too.
_ORG_QUOTED_RE = re.compile(_ORG_QUOTED)

#: Legal-form prefix + separator + name, e.g. `ООО «Ромашка»`. The abbreviated
#: prefixes are matched case-insensitively and need no inflection.
_ORG_ABBREV_RE = re.compile(
    rf"\b(?:{_ORG_ABBREV})\b\.?[\s,;\-–—]*(?:{_ORG_NAME_PART})",
    re.IGNORECASE,
)

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


def _lemmatize(morph: MorphVocab, word: str) -> str:
    """Return the dictionary form of `word`, lower-cased; the word itself if unknown."""
    parses = morph(word)
    return parses[0].normal_form if parses else word.lower().replace("ё", "е")


def _lemma_close(morph: MorphVocab, token: str, lemma: str) -> bool:
    """
    Whether `token` is (a form of) `lemma` — by exact lemma or close spelling.

    The similarity branch admits typos ("акциноерным" → "акционерный") and
    unusual forms the dictionary does not lemmatise; the threshold is tuned so
    random adjectives ending in "-ым" are rejected (see `_ORG_LEMMA_RATIO`).
    """
    normalized = _lemmatize(morph, token)
    if normalized == lemma:
        return True
    return difflib.SequenceMatcher(None, normalized, lemma).ratio() >= _ORG_LEMMA_RATIO


@lru_cache(maxsize=1)
def _register_lexicon() -> frozenset[str]:
    """Every known word of a spelled-out legal form, for the trigram pre-filter."""
    return frozenset(lemma for form in _ORG_FULL_FORMS for lemma in form if len(lemma) >= 4)


def _suspicious_positions(text: str) -> Iterator[int]:
    """
    Yield the start offset of each token that could begin a spelled-out form.

    A cheap pre-filter: a token must share its leading trigram with some known
    legal-form word, so a paragraph with no organisation-like word never runs the
    (much costlier) lemmatiser. This keeps the per-request cost near zero.
    """
    lexicon = _register_lexicon()
    prefixes = {word[:3] for word in lexicon}
    for match in re.finditer(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]*", text):
        if match.group(0).lower()[:3] in prefixes:
            yield match.start()


def _match_full_form(morph: MorphVocab, text: str, start: int) -> tuple[int, int] | None:
    """
    If a spelled-out legal form starts at `start`, return its `(start, end)`.

    Words are matched one by one (their lemmas must equal or resemble the lemma
    of the form), separated by `_ORG_GAP_RE`, over each form length — shorter
    forms are tried first so "Публичное акционерное общество" is not reported as
    just "…акционерное общество".
    """
    for length in _ORG_FORM_LENGTHS:
        cursor = start
        position = -1
        for index in range(length):
            word = _WORD_RE.match(text, cursor)
            if word is None:
                break
            if index == 0:
                position = word.start()
            cursor = word.end()
            if index < length - 1:
                gap = _ORG_GAP_RE.match(text, cursor)
                if gap is None:
                    break
                cursor = gap.end()
        else:
            tokens = _tokens(text, position, cursor)
            for form in (form for form in _ORG_FULL_FORMS if len(form) == length):
                if all(_lemma_close(morph, token, lemma) for token, lemma in zip(tokens, form, strict=False)):
                    return position, cursor
    return None


def _tokens(text: str, start: int, end: int) -> list[str]:
    """Return the word tokens of `text[start:end]`, in order."""
    return _WORD_RE.findall(text, start, end)


def _org_name_span(text: str, end: int) -> int:
    r"""
    End of the organisation name that follows a legal form at `end`.

    A separator (`[\s,;\-–—]*`) may sit between the form and its name ("АО,
    «Росхим»"). Prefers a quoted name (quotes included) and falls back to a
    capitalised word chain. Returns `end` when there is no name, so the legal
    form itself is still masked ("индивидуальный предприниматель" without a
    following brand).
    """
    cursor = end
    while cursor < len(text) and text[cursor] in _ORG_SEPARATORS:
        cursor += 1
    tail = text[cursor:]
    quoted = _ORG_QUOTED_RE.match(tail)
    if quoted is not None:
        return cursor + quoted.end()
    name = _ORG_NAME_RE.match(tail)
    if name is not None:
        return cursor + name.end()
    return end


@lru_cache(maxsize=1)
def _always_mask_re() -> re.Pattern[str] | None:
    """
    Case-insensitive pattern for the always-mask organisation list.

    Applied last, after every rule-based span has been merged, so a brand that
    must never leak is masked even when no legal-form prefix or extractor
    matched it (bare "Росхим", lowercase "росхим", "АО Росхим", …). An optional
    Cyrillic tail covers inflected mentions ("Росхима", "Росхиме", "Росхимом").
    """
    if not ALWAYS_MASK_ORGANIZATIONS:
        return None
    names = "|".join(re.escape(name) for name in sorted(ALWAYS_MASK_ORGANIZATIONS, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-zА-Яа-яЁё0-9])(?:{names})[а-яё]*(?![A-Za-zА-Яа-яЁё0-9])", re.IGNORECASE)


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
        # Abbreviated prefixes: the regex already consumes the quoted name and
        # its quotes, so the span is used as-is (no trailing-quote trimming).
        for regex in (_ORG_ABBREV_RE, _ORG_LEAD_RE):
            for match in regex.finditer(text):
                yield Entity(ORGANIZATION, match.group(0), match.start(), match.end())
        yield from self._find_full_forms(text)

    def _find_full_forms(self, text: str) -> Iterator[Entity]:
        """Spelled-out legal forms in any case ("Акционерным обществом …")."""
        morph = self._morph
        for start in _suspicious_positions(text):
            matched = _match_full_form(morph, text, start)
            if matched is None:
                continue
            span_start, span_end = matched
            span_end = _org_name_span(text, span_end)
            yield Entity(ORGANIZATION, text[span_start:span_end], span_start, span_end)

    def _find_always_masked(self, text: str) -> Iterator[Entity]:
        """Brands from ALWAYS_MASK_ORGANIZATIONS, regardless of context/case."""
        if ORGANIZATION not in self.labels:
            return
        regex = _always_mask_re()
        if regex is None:
            return
        for match in regex.finditer(text):
            yield Entity(ORGANIZATION, match.group(0), match.start(), match.end())

    # -- public API -----------------------------------------------------------

    def detect(self, text: str) -> list[Entity]:
        """Return non-overlapping entities found in `text`, sorted by position."""
        raw: list[Entity] = []
        raw.extend(self._find_emails(text))
        raw.extend(self._find_phones(text))
        raw.extend(self._find_persons(text))
        raw.extend(self._find_addresses(text))
        raw.extend(self._find_organizations(text))
        merged = _merge_spans(raw)
        # Last resort: brands that must never leak, even when no rule matched
        # them on their own (bare name, no legal-form prefix). Existing spans win.
        return _merge_spans([*merged, *self._find_always_masked(text)])

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
    """
    Reject stubs that are just a stray word or a bare number.

    A span must either carry an explicit address keyword ("г.", "ул.", …) or mix
    letters with digits ("Ленина 5"). A lone number ("270023", "6402 270023") is
    *not* an address — `natasha` emits digit-only `AddrPart`s for passport /
    index-like sequences, which must not be masked.
    """
    if not _WORD_CHAR.search(raw):
        return False
    has_keyword = any(
        marker in raw.lower() for marker in ("г.", "г ", "ул", "д.", "д ", "просп", "пер", "шоссе", "обл", "р-н", "кв")
    )
    if has_keyword:
        return True
    has_letters = any(char.isalpha() for char in raw)
    has_digits = any(char.isdigit() for char in raw)
    return has_letters and has_digits


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
