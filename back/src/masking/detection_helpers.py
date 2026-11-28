"""
Structural detection helpers for the deterministic Russian PII pipeline.

Pure regexes, span containers and text-shape predicates shared by the masker
and the exception matcher. Curated vocabulary stays in the immutable config
snapshot; this module holds algorithm only.
"""

import difflib
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

from natasha import MorphVocab

if TYPE_CHECKING:
    from .config import MaskingConfig

# --- Label vocabulary --------------------------------------------------------
PERSON = "person"
PHONE = "phone"
EMAIL = "email"
ADDRESS = "address"
ORGANIZATION = "organization"
LINK = "link"


def _cfg() -> MaskingConfig:
    """
    Resolve the immutable config snapshot through `rule_based`.

    The late binding preserves the established test/config override hook after
    the helper split; no config data is duplicated in this module.
    """
    from .rule_based import _cfg as _canonical_cfg  # noqa: PLC0415 - deliberate late binding

    return _canonical_cfg()


def _normalize(value: str) -> str:
    """NFC + trim + collapse whitespace. `ё`/`е` are NOT equivalent."""
    return " ".join(unicodedata.normalize("NFC", value).split())


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
_ADDRESS_SENTENCE_END_RE = re.compile(r"[.!?\n]+\s*")


@lru_cache(maxsize=1)
def _address_abbreviation_dots() -> frozenset[str]:
    """Abbreviation dots («г.», «ул.» …) that are not sentence ends."""
    return frozenset(f"{word}." for word in _cfg().address_abbreviations)


@lru_cache(maxsize=1)
def _address_candidate_re() -> re.Pattern[str]:
    """
    Cheap, config-driven markers worth sending to the expensive `AddrExtractor`.

    Every alternative comes from the validated lexicon: whole-word markers and
    hyphenated literals from `address_keywords`, bare/dotted abbreviations from
    `address_abbreviations`, plus the `адрес:` label and a 6-digit postcode.
    Unicode word boundaries keep «ш» inside «Строймонтаж» out, and a bare
    abbreviation additionally needs a capitalised word or digit after it so
    «ш consulted» cannot nominate. Case-insensitivity is scoped to the
    lexicon tokens — the continuation lookahead stays case-sensitive.

    A match ONLY nominates a range for `AddrExtractor`; it never auto-masks
    the sentence. Built lazily so importing this module needs no config.
    """
    cfg = _cfg()
    full_words = sorted(
        {word for word in cfg.address_keywords if len(word) >= 3 and word.isalpha()},
        key=len,
        reverse=True,
    )
    literals = sorted(
        {
            word
            for word in cfg.address_keywords
            if not word.isalpha() and word.strip(" .") and not word.endswith((" ", "."))
        },
        key=len,
        reverse=True,
    )
    abbreviations = sorted(cfg.address_abbreviations, key=len, reverse=True)
    markers = "|".join([*(re.escape(word) for word in full_words), *(re.escape(word) for word in literals)])
    abbr = "|".join(re.escape(word) for word in abbreviations)
    alternatives: list[str] = []
    if markers:
        alternatives.append(rf"(?i:\b(?:{markers})\b)")
    if abbr:
        # A preceding dot is excluded so the tail of «и т.д.» is not a marker.
        alternatives.append(rf"(?<!\.)(?i:\b(?:{abbr}))\s*\.")
        alternatives.append(rf"(?<!\.)(?i:\b(?:{abbr})\b)\s+(?=[А-ЯЁA-Z0-9])")
    alternatives.append(r"(?i:\bадрес\w*)\s*:")
    alternatives.append(r"\b\d{6}\b")
    return re.compile("(?:" + "|".join(alternatives) + ")")


#: Bounded lookback/lookahead for the soft-newline continuation check.
_ADDRESS_LINE_CONTEXT_CHARS = 80


def _address_continues_after_newline(text: str, position: int) -> bool:
    """
    Whether the line starting at `position` continues an address from the line above.

    A trailing comma or a marker/digit at the start of the next line is
    the signal; both sides are bounded so this stays O(1) per newline.
    """
    head = text[max(0, position - _ADDRESS_LINE_CONTEXT_CHARS) : position].rstrip()
    if head.endswith(","):
        return True
    tail = text[position : position + _ADDRESS_LINE_CONTEXT_CHARS].lstrip()
    return bool(tail) and (tail[0].isdigit() or bool(_address_candidate_re().match(tail)))


def _append_address_range(ranges: list[tuple[int, int]], start: int, end: int) -> None:
    """Append a nominated range, merging with the touching previous one."""
    if ranges and start <= ranges[-1][1]:
        ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
    else:
        ranges.append((start, end))


def _address_candidate_ranges(text: str) -> Iterator[tuple[int, int]]:
    """
    Sentence-sized ranges that may contain an AddrExtractor match.

    `AddrExtractor` costs ~0.2 ms per character; on a 500 KB document that is
    ~100 s and ~10 GB peak RSS. Only sentences carrying a configured address
    marker are fed to it (the `адрес:` line path is handled separately).
    Abbreviation dots are not sentence ends, and a bare newline is a soft
    boundary when the next line continues the address — otherwise a wrapped
    address reaches the extractor as unrecognisable fragments. Touching ranges
    are merged so an address split across two markers stays one input. Text
    with NO marker causes no AddrExtractor call — the fast path (§9.2).
    """
    candidate_re = _address_candidate_re()
    dots = _address_abbreviation_dots()
    ranges: list[tuple[int, int]] = []
    start = 0
    for match in _ADDRESS_SENTENCE_END_RE.finditer(text):
        # Include the dot itself so «кв.» is recognised as an abbreviation,
        # not as a sentence end that would truncate the address.
        separator = text[match.start() : match.end()]
        prefix = text[max(0, match.start() - 8) : match.start() + 1].lower().strip()
        if any(prefix.endswith(dot) for dot in dots):
            continue
        if (
            "\n" in separator
            and not any(char in separator for char in ".!?")
            and _address_continues_after_newline(text, match.end())
        ):
            continue
        end = match.end()
        if candidate_re.search(text, start, end):
            _append_address_range(ranges, start, end)
        start = end
    if start < len(text) and candidate_re.search(text, start):
        _append_address_range(ranges, start, len(text))
    yield from ranges


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


@lru_cache(maxsize=256)
def _suspicious_positions(text: str) -> tuple[int, ...]:
    """Cheap trigram pre-filter before the costly lemmatiser (bounded cache)."""
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
