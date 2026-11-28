"""
Deterministic, rule-based PII masking for Russian text (Phase 4).

Hybrid of morphological extraction (`natasha`), `phonenumbers` and targeted
regular expressions; fully deterministic, no model download, no GPU. All
curated vocabulary and exception data lives in `config/masking/` and is loaded
into one immutable snapshot at startup (`masking/config.py`). Algorithms,
normalization, span merging and safe matching stay in Python — configuration
is literal data only, never executable code or regex.

Structural helpers live in `detection_helpers`; exception literals and cheap
preconditions in `exceptions`. Names historically imported from this module
are re-exported here to keep the public API stable.
"""

import re
import threading
import warnings
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace

import phonenumbers
from natasha import AddrExtractor, MorphVocab, NamesExtractor

from src.config import get_settings

from .config import MaskingConfig, get_config
from .detection_helpers import (  # noqa: F401 - re-exported for backwards compatibility
    _ADDITIONAL_TRIM,
    _ADDRESS_LINE_CONTEXT_CHARS,
    _ADDRESS_LINE_RE,
    _ADDRESS_SENTENCE_END_RE,
    _EMAIL_RE,
    _INITIAL,
    _INITIALS_SURNAME_RE,
    _MIN_ADDRESS_PART,
    _NAME_PAIR_RE,
    _ORG_GAP_RE,
    _ORG_LEMMA_RATIO,
    _ORG_NAME,
    _ORG_NAME_PART,
    _ORG_NAME_RE,
    _ORG_QUOTED,
    _ORG_QUOTED_RE,
    _SURNAME_INITIALS_RE,
    _URL_RE,
    _WORD_CHAR,
    _WORD_RE,
    ADDRESS,
    EMAIL,
    LINK,
    ORGANIZATION,
    PERSON,
    PHONE,
    Entity,
    Span,
    _address_abbreviation_dots,
    _address_candidate_ranges,
    _address_candidate_re,
    _address_continues_after_newline,
    _address_value_end,
    _append_address_range,
    _form_lengths,
    _group_address_ranges,
    _is_name_token,
    _legal_lexicon,
    _lemma_close,
    _lemmatize,
    _looks_like_address,
    _match_full_form,
    _merge_address_parts,
    _merge_spans,
    _normalize,
    _org_name_span,
    _surname_accepted,
    _suspicious_positions,
    _trim_span,
    _trim_url,
)
from .detectors import DETECTORS
from .exceptions import (  # noqa: F401 - re-exported for backwards compatibility
    _BOUNDARY_LEFT,
    _BOUNDARY_RIGHT,
    _DETECTOR_PRECONDITIONS,
    _cheap_prefilter,
    _compile_exception_literals,
    _exception_pattern,
    _ExceptionPattern,
    _suppressed,
)
from .windowed import dedupe_boundary_spans, iter_windows

#: Stable detector run order (the profile abstraction was removed in Phase 4).
DETECTOR_ORDER: tuple[str, ...] = ("email", "link", "phone", "person", "address", "organization")


def _cfg() -> MaskingConfig:
    return get_config()


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
        self._always_patterns = {label: _exception_pattern(values) for label, values in cfg.always_mask.items()}
        self._never_patterns = {label: _exception_pattern(values) for label, values in cfg.never_mask.items()}

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
        for start, end in _address_candidate_ranges(text):
            fragment = text[start:end]
            for entity in _merge_address_parts(self._addresses(fragment), fragment):
                yield replace(entity, start=entity.start + start, end=entity.end + start)
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

    def _detect_window(self, text: str, allowed: frozenset[str] | None = None) -> list[Entity]:
        """Run the detector registry once over one window (local offsets)."""
        raw: list[Entity] = []
        for detector_id in DETECTOR_ORDER:
            if allowed is not None and detector_id not in allowed:
                continue
            raw.extend(DETECTORS[detector_id].run(self, text))
        return raw

    def _detect_long(
        self,
        text: str,
        checkpoint: Callable[[], None] | None,
    ) -> list[Entity]:
        """
        Windowed detection for large text (Phase 7 §9.1).

        `AddrExtractor` is superlinear on a whole document, so each window is
        bounded; spans are re-based to absolute offsets and exact boundary
        duplicates are dropped before the shared overlap merge. Cancellation
        is checked between windows (worker cooperative checkpoints).
        """
        settings = get_settings()
        raw: list[Entity] = []
        for offset, window in iter_windows(
            text,
            min_chars=settings.detection_min_chars,
            chunk_chars=settings.detection_chunk_chars,
            overlap_chars=settings.detection_overlap_chars,
        ):
            if checkpoint is not None:
                checkpoint()
            for entity in self._detect_window(window):
                raw.append(replace(entity, start=entity.start + offset, end=entity.end + offset))
        unique = dedupe_boundary_spans(raw, lambda e: (e.label, e.start, e.end, e.text))
        return _merge_spans(unique)

    def detect(
        self,
        text: str,
        *,
        checkpoint: Callable[[], None] | None = None,
        prefilter: bool = False,
    ) -> list[Entity]:
        """
        Non-overlapping entities, with never_mask suppression and force-mask.

        For text at/below `DETECTION_MIN_CHARS` this is the original single-pass
        path; `prefilter=True` (OOXML per-paragraph calls) additionally skips
        detectors whose input cannot possibly match. Larger text uses bounded
        overlapping windows. `checkpoint` is invoked between windows (§9.1).
        """
        if len(text) > get_settings().detection_min_chars:
            merged = self._detect_long(text, checkpoint)
        else:
            allowed = _cheap_prefilter(text) if prefilter else None
            merged = _merge_spans(self._detect_window(text, allowed))
        kept = [entity for entity in merged if not _suppressed(entity, self._never_patterns)]
        # always_mask runs over the FULL text, never a window / prefiltered
        # subset: a configured value must never be suppressed by this shortcut.
        return _merge_spans([*kept, *self._find_always_masked(text)])

    def mask(self, text: str, entities: list[Entity] | None = None) -> str:
        """
        Keep the deprecated reference renderer out of request/worker paths.

        It rewrites the text with one full-string slice per entity
        (O(|text| * entities)); production uses `service.mask_text`, a validated
        single join (O(|text| + entities)). Tests and benchmarks retain this
        method only as a readable way to assert `detect()` output. A static CI
        guard fails if it is referenced anywhere under `back/src`.
        """
        warnings.warn(
            "RuleBasedMasker.mask() is deprecated: it copies the whole text "
            "once per entity and is kept only as a test/benchmark reference "
            "for detect(). Use src.masking.service.mask_text() in production.",
            DeprecationWarning,
            stacklevel=2,
        )
        if entities is None:
            entities = self.detect(text)
        masked = text
        for entity in sorted(entities, key=lambda item: item.start, reverse=True):
            masked = masked[: entity.start] + f"[{entity.label.upper()}]" + masked[entity.end :]
        return masked


_THREAD_LOCAL = threading.local()


def _new_masker() -> RuleBasedMasker:
    masker = RuleBasedMasker()
    _THREAD_LOCAL.masker = masker
    return masker


def get_masker() -> RuleBasedMasker:
    """
    One masker **per thread** (Phase 7 §9.2).

    `natasha`'s `NamesExtractor`/`AddrExtractor` (yargy) keep mutable parser
    state and are not proven thread-safe, while the sync CPU pool and the
    worker run detection concurrently. A process-wide `@cache` instance was a
    latent race. `threading.local()` gives each worker thread its own
    `MorphVocab` — the memory tradeoff is explicit: ~110 MB per instance, so
    keep `JOBS_CONCURRENCY` conservative (default 4, measurement-derived —
    see `docs/PERFORMANCE.md`).
    """
    masker = getattr(_THREAD_LOCAL, "masker", None)
    return masker if masker is not None else _new_masker()


def reset_masker() -> None:
    """Drop this thread's cached masker (tests / config reload)."""
    if hasattr(_THREAD_LOCAL, "masker"):
        del _THREAD_LOCAL.masker
