"""
Deterministic, rule-based PII masking for Russian text (Phase 4).

Hybrid of morphological extraction (`natasha`), `phonenumbers` and targeted
regular expressions; fully deterministic, no model download, no GPU. All
curated vocabulary and exception data lives in `config/masking/` and is loaded
into one immutable snapshot at startup (`masking/config.py`). Algorithms,
normalization, span merging and safe matching stay in Python — configuration
is literal data only, never executable code or regex.
"""

import difflib
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from typing import Protocol

import phonenumbers
from natasha import AddrExtractor, MorphVocab, NamesExtractor

from .config import ExceptionValue, MaskingConfig, get_config
from .detectors import DETECTORS

# --- Label vocabulary --------------------------------------------------------
PERSON = "person"
PHONE = "phone"
EMAIL = "email"
ADDRESS = "address"
ORGANIZATION = "organization"
LINK = "link"

#: Stable detector run order (the profile abstraction was removed in Phase 4).
DETECTOR_ORDER: tuple[str, ...] = ("email", "link", "phone", "person", "address", "organization")

# --- Structural regexes (algorithm, not vocabulary) -------------------------
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+",
)
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s/$.?#].[^\s]*", re.IGNORECASE)
_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]*")
_WORD_CHAR = re.compile(r"[A-Za-zА-Яа-яЁё0-9]")
_ORG_GAP_RE = re.compile(r"[\s,.;:()\-–—]+")
_ORG_NAME = r"[А-ЯЁ][\w\-]*(?:\s+[А-ЯЁ][\w\-]*){0,4}"
_ORG_QUOTED = (
    r"\"[^\"\n]{1,80}\""
    r"|«[^»\n]{1,80}»"
    r"|„[^“\n]{1,80}“"
    r"|“[^”\n]{1,80}”"
    r"|‘[^’\n]{1,80}’"
    r"|'[^'\n]{1,80}'"
    r"|<[^>\n]{1,80}>"
)
_ORG_NAME_PART = rf"(?:{_ORG_QUOTED})|{_ORG_NAME}"
_ORG_NAME_RE = re.compile(_ORG_NAME)
_ORG_QUOTED_RE = re.compile(_ORG_QUOTED)
_ORG_LEMMA_RATIO = 0.9
_MIN_ADDRESS_PART = 3
_ADDITIONAL_TRIM = "."
_INITIAL = r"[А-ЯЁ]\.\s*"
_INITIALS_SURNAME_RE = re.compile(rf"(?<!\w){_INITIAL}{_INITIAL}(?P<surname>[А-ЯЁ][а-яё]+)")
_SURNAME_INITIALS_RE = re.compile(rf"(?<!\w)(?P<surname>[А-ЯЁ][а-яё]+)\s+{_INITIAL}{_INITIAL}")
_NAME_PAIR_RE = re.compile(r"(?=(?P<first>[А-ЯЁ][а-яё]+)[ \t]+(?P<last>[А-ЯЁ][а-яё]+))")
_ADDRESS_LINE_RE = re.compile(r"(?im)\bадрес\w*\s*:\s*(?P<value>[^\n]+)")


def _cfg() -> MaskingConfig:
    return get_config()


def _normalize(value: str) -> str:
    """NFC + trim + collapse whitespace. `ё`/`е` are NOT equivalent."""
    return " ".join(unicodedata.normalize("NFC", value).split())


# --- span containers ---------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    """A detected span in the *original* text (offsets are absolute)."""

    label: str
    text: str
    start: int
    end: int
    score: float = 1.0


class Span(Protocol):
    """Structural view of a `natasha` match."""

    start: int
    stop: int


def _merge_spans(entities: Iterable[Entity]) -> list[Entity]:
    """Non-overlapping, sorted spans: earliest/longest/highest-score wins."""
    ordered = sorted(
        enumerate(entities),
        key=lambda item: (item[1].start, -(item[1].end - item[1].start), -item[1].score, item[0]),
    )
    result: list[Entity] = []
    kept_end = -1
    for _index, entity in ordered:
        if entity.start < kept_end:
            continue
        result.append(entity)
        kept_end = max(kept_end, entity.end)
    return sorted(result, key=lambda entity: entity.start)


def _lemmatize(morph: MorphVocab, word: str) -> str:
    parses = morph(word)
    return parses[0].normal_form if parses else word.lower().replace("ё", "е")


def _lemma_close(morph: MorphVocab, token: str, lemma: str) -> bool:
    normalized = _lemmatize(morph, token)
    if normalized == lemma:
        return True
    return difflib.SequenceMatcher(None, normalized, lemma).ratio() >= _ORG_LEMMA_RATIO


def _form_lengths() -> tuple[int, ...]:
    return tuple(sorted({len(form) for form in _cfg().org_legal_phrases}))


def _legal_lexicon() -> frozenset[str]:
    return frozenset(lemma for form in _cfg().org_legal_phrases for lemma in form if len(lemma) >= 4)


@cache
def _suspicious_positions(text: str) -> tuple[int, ...]:
    """Cheap trigram pre-filter before the costly lemmatiser."""
    lexicon = _legal_lexicon()
    prefixes = {word[:3] for word in lexicon}
    return tuple(
        match.start()
        for match in re.finditer(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]*", text)
        if match.group(0).lower()[:3] in prefixes
    )


def _match_full_form(morph: MorphVocab, text: str, start: int) -> tuple[int, int] | None:
    phrases = _cfg().org_legal_phrases
    for length in _form_lengths():
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
            tokens = _WORD_RE.findall(text, position, cursor)
            for form in (form for form in phrases if len(form) == length):
                if all(_lemma_close(morph, token, lemma) for token, lemma in zip(tokens, form, strict=False)):
                    return position, cursor
    return None


def _org_name_span(text: str, end: int) -> int:
    cfg = _cfg()
    cursor = end
    while cursor < len(text) and text[cursor] in cfg.org_separators:
        cursor += 1
    tail = text[cursor:]
    quoted = _ORG_QUOTED_RE.match(tail)
    if quoted is not None:
        return cursor + quoted.end()
    name = _ORG_NAME_RE.match(tail)
    if name is not None:
        return cursor + name.end()
    return end


def _address_value_end(text: str, start: int, end: int) -> int:
    result = end
    abbreviations = _cfg().address_abbreviations
    trim = _cfg().address_trim_chars + _ADDITIONAL_TRIM
    for match in re.finditer(r"([^\s.]*)\.\s+(?=[А-ЯЁA-Z])", text[start:end]):
        word = match.group(1).lower()
        if word not in abbreviations:
            result = start + match.start() + len(match.group(1))
            break
    while result > start and text[result - 1] in trim:
        result -= 1
    return result


def _trim_url(text: str, start: int, end: int) -> tuple[int, int]:
    trim = _cfg().url_trim_chars
    while end > start and text[end - 1] in trim:
        end -= 1
    return start, end


def _is_name_token(morph: MorphVocab, token: str) -> bool:
    if not token or not token[0].isupper():
        return False
    return any(marker in str(parse.tag) for parse in morph(token) for marker in ("Name", "Surn", "Patr"))


def _surname_accepted(morph: MorphVocab, token: str) -> bool:
    if _is_name_token(morph, token):
        return True
    if not re.fullmatch(r"[А-ЯЁ][а-яё]+", token):
        return False
    if _lemmatize(morph, token).lower() in _cfg().person_roles:
        return False
    tags = [str(parse.tag) for parse in morph(token)]
    if any("Geox" in tag for tag in tags):
        return False
    if all(tag.startswith(("INTJ", "VERB", "PREP", "ADJF,Qual")) for tag in tags):
        return False
    return not all("inan" in tag and "anim" not in tag for tag in tags)


# --- exceptions (always_mask / never_mask) -----------------------------------


def _exception_regex(values: tuple[ExceptionValue, ...]) -> re.Pattern[str] | None:
    """Word-boundary-safe literal matcher; never a substring match by default."""
    if not values:
        return None
    parts: list[str] = []
    for value in values:
        literals = (value.value, *value.variants)
        for literal in literals:
            parts.append(re.escape(_normalize(literal)))
    pattern = "|".join(sorted(set(parts), key=len, reverse=True))
    return re.compile(rf"(?<![\wА-Яа-яЁё])(?:{pattern})(?![\wА-Яа-яЁё])", re.IGNORECASE)


def _suppressed(entity: Entity, patterns: dict[str, re.Pattern[str] | None]) -> bool:
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


class RuleBasedMasker:
    """
    Deterministic Russian PII masker built from one immutable config snapshot.

    Construct once per process (natasha's MorphVocab is expensive). `labels`
    selects a label subset for tests; production uses the full catalogue.
    """

    def __init__(self, labels: Iterable[str] | None = None) -> None:
        cfg = _cfg()
        self.config = cfg
        all_labels = cfg.label_ids()
        if labels is None:
            self.labels = all_labels
        else:
            wanted = tuple(label.strip() for label in labels if label.strip())
            unknown = [label for label in wanted if label not in all_labels]
            if unknown:
                msg = f"Неизвестный тип сущности: «{unknown[0]}». Допустимые значения: {', '.join(all_labels)}."
                raise ValueError(msg)
            if len(set(wanted)) != len(wanted):
                msg = f"Тип сущности указан больше одного раза: {', '.join(wanted)}."
                raise ValueError(msg)
            self.labels = wanted or all_labels
        self._morph = MorphVocab()
        self._names = NamesExtractor(self._morph)
        self._addresses = AddrExtractor(self._morph)
        # Compiled from validated literal data at startup.
        abbrev = "|".join(re.escape(item) for item in cfg.org_abbreviations)
        self._org_abbrev_re = re.compile(rf"\b(?:{abbrev})\b\.?[\s,;\-–—]*(?:{_ORG_NAME_PART})", re.IGNORECASE)
        lead = "|".join(re.escape(item) for item in cfg.org_lead_words)
        self._org_lead_re = re.compile(rf"\b(?:{lead})\s+(?:{_ORG_QUOTED}|{_ORG_NAME})")
        self._always_patterns = {label: _exception_regex(values) for label, values in cfg.always_mask.items()}
        self._never_patterns = {label: _exception_regex(values) for label, values in cfg.never_mask.items()}

    @classmethod
    def for_labels(cls, labels: Iterable[str]) -> RuleBasedMasker:
        """Build a masker narrowed to a label subset (for tests)."""
        return cls(labels)

    # -- span providers -------------------------------------------------------

    def _find_emails(self, text: str) -> Iterator[Entity]:
        if EMAIL not in self.labels:
            return
        for match in _EMAIL_RE.finditer(text):
            yield Entity(EMAIL, match.group(0), match.start(), match.end())

    def _find_links(self, text: str) -> Iterator[Entity]:
        if LINK not in self.labels:
            return
        for match in _URL_RE.finditer(text):
            start, end = _trim_url(text, match.start(), match.end())
            if end > start:
                yield Entity(LINK, text[start:end], start, end)

    def _find_phones(self, text: str) -> Iterator[Entity]:
        if PHONE not in self.labels:
            return
        for match in phonenumbers.PhoneNumberMatcher(text, "RU"):
            yield Entity(PHONE, match.raw_string, match.start, match.end)

    def _find_persons(self, text: str) -> Iterator[Entity]:
        if PERSON not in self.labels:
            return
        morph = self._morph
        for match in self._names(text):
            fact = match.fact
            if not fact.first or not _is_name_token(morph, fact.first):
                continue
            if fact.middle and fact.last:
                if not _is_name_token(morph, fact.middle):
                    continue
                if not _surname_accepted(morph, fact.last):
                    continue
                yield Entity(PERSON, text[match.start : match.stop], match.start, match.stop)
                continue
            if not (fact.last or fact.middle):
                continue
            other = fact.last or fact.middle
            if not _surname_accepted(morph, other):
                continue
            yield Entity(PERSON, text[match.start : match.stop], match.start, match.stop)
        for pattern in (_INITIALS_SURNAME_RE, _SURNAME_INITIALS_RE):
            for match in pattern.finditer(text):
                span = text[match.start() : match.end()].rstrip()
                end = match.start() + len(span)
                if not _surname_accepted(morph, match.group("surname")):
                    continue
                yield Entity(PERSON, text[match.start() : end], match.start(), end)
        for match in _NAME_PAIR_RE.finditer(text):
            first, last = match.group("first"), match.group("last")
            if not _is_name_token(morph, first) or not _is_name_token(morph, last):
                continue
            end = match.start("last") + len(last)
            yield Entity(PERSON, text[match.start() : end], match.start(), end)

    def _find_addresses(self, text: str) -> Iterator[Entity]:
        if ADDRESS not in self.labels:
            return
        yield from _merge_address_parts(self._addresses(text), text)
        for match in _ADDRESS_LINE_RE.finditer(text):
            start, end = match.start("value"), match.end("value")
            end = _address_value_end(text, start, end)
            if end > start and _looks_like_address(text[start:end]):
                yield Entity(ADDRESS, text[start:end], start, end)

    def _find_organizations(self, text: str) -> Iterator[Entity]:
        if ORGANIZATION not in self.labels:
            return
        for regex in (self._org_abbrev_re, self._org_lead_re):
            for match in regex.finditer(text):
                yield Entity(ORGANIZATION, match.group(0), match.start(), match.end())
        yield from self._find_full_forms(text)

    def _find_full_forms(self, text: str) -> Iterator[Entity]:
        morph = self._morph
        for start in _suspicious_positions(text):
            matched = _match_full_form(morph, text, start)
            if matched is None:
                continue
            span_start, span_end = matched
            span_end = _org_name_span(text, span_end)
            yield Entity(ORGANIZATION, text[span_start:span_end], span_start, span_end)

    def _find_always_masked(self, text: str) -> Iterator[Entity]:
        """Force-mask configured literal values that no detector matched."""
        for label, pattern in self._always_patterns.items():
            if label not in self.labels or pattern is None:
                continue
            for match in pattern.finditer(text):
                yield Entity(label, match.group(0), match.start(), match.end())

    # -- public API -----------------------------------------------------------

    def detect(self, text: str) -> list[Entity]:
        """Non-overlapping entities, with never_mask suppression and force-mask."""
        raw: list[Entity] = []
        for detector_id in DETECTOR_ORDER:
            raw.extend(DETECTORS[detector_id].run(self, text))
        merged = _merge_spans(raw)
        kept = [entity for entity in merged if not _suppressed(entity, self._never_patterns)]
        return _merge_spans([*kept, *self._find_always_masked(text)])

    def mask(self, text: str, entities: list[Entity] | None = None) -> str:
        if entities is None:
            entities = self.detect(text)
        masked = text
        for entity in sorted(entities, key=lambda item: item.start, reverse=True):
            masked = masked[: entity.start] + f"[{entity.label.upper()}]" + masked[entity.end :]
        return masked


def _merge_address_parts(parts: Iterable[Span], text: str) -> Iterator[Entity]:
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
    current_start: int | None = None
    current_stop = -1
    for match in matches:
        start, stop = int(match.start), int(match.stop)
        if current_start is None:
            current_start, current_stop = start, stop
            continue
        if 0 <= start - current_stop <= 3:
            current_stop = stop
        else:
            yield current_start, current_stop
            current_start, current_stop = start, stop
    if current_start is not None:
        yield current_start, current_stop


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    trim = _cfg().address_trim_chars
    while start < end and text[start] in trim:
        start += 1
    while end > start and text[end - 1] in trim:
        end -= 1
    return start, end


def _looks_like_address(raw: str) -> bool:
    if not _WORD_CHAR.search(raw):
        return False
    if any(marker in raw.lower() for marker in _cfg().address_keywords):
        return True
    has_letters = any(char.isalpha() for char in raw)
    has_digits = any(char.isdigit() for char in raw)
    return has_letters and has_digits


@cache
def get_masker() -> RuleBasedMasker:
    """Process-wide masker (one immutable config snapshot)."""
    return RuleBasedMasker()
