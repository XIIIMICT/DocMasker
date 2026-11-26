"""Phase 7 §9.1/§9.2: windowed detection, prefilter, thread-local masker."""

import itertools
import threading

import pytest

from src.config import Settings
from src.masking import rule_based
from src.masking.rule_based import Entity, RuleBasedMasker, get_masker
from src.masking.windowed import dedupe_boundary_spans, iter_windows

_ALIASES = {
    "detection_min_chars": "DETECTION_MIN_CHARS",
    "detection_chunk_chars": "DETECTION_CHUNK_CHARS",
    "detection_overlap_chars": "DETECTION_OVERLAP_CHARS",
}


@pytest.fixture
def small_windows(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Force windowing on short strings so boundary tests stay fast."""
    settings = Settings.model_validate(
        {
            "DETECTION_MIN_CHARS": 64,
            "DETECTION_CHUNK_CHARS": 128,
            "DETECTION_OVERLAP_CHARS": 48,
        }
    )
    monkeypatch.setattr(rule_based, "get_settings", lambda: settings)
    return settings


# --- iter_windows ------------------------------------------------------------


def test_iter_windows_short_text_is_one_window() -> None:
    assert list(iter_windows("abc", min_chars=200, chunk_chars=64, overlap_chars=16)) == [(0, "abc")]


def test_iter_windows_covers_every_character_without_gaps() -> None:
    text = ("Предложение номер двадцать пять. " * 40).strip()
    windows = list(iter_windows(text, min_chars=50, chunk_chars=128, overlap_chars=32))
    assert windows
    # Union of all windows equals the text; first starts at 0, last ends at len.
    assert windows[0][0] == 0
    last_offset, last = windows[-1]
    assert last_offset + len(last) == len(text)
    for offset, window in windows:
        assert window == text[offset : offset + len(window)]
    # Consecutive windows overlap (never a silent gap).
    for (offset_a, win_a), (offset_b, _win_b) in itertools.pairwise(windows):
        assert offset_b < offset_a + len(win_a)


def test_iter_windows_prefers_structural_boundaries() -> None:
    text = ("x" * 100 + "\n") * 8
    _offset, window = next(iter(iter_windows(text, min_chars=50, chunk_chars=128, overlap_chars=16)))
    assert window.endswith("\n")


# --- boundary dedupe ---------------------------------------------------------


def test_dedupe_boundary_spans_is_deterministic() -> None:
    spans = [
        Entity("person", "Иван", 10, 14),
        Entity("person", "Иван", 10, 14),  # duplicate from the overlapping window
        Entity("phone", "+7", 20, 22),
    ]
    result = dedupe_boundary_spans(spans, lambda e: (e.label, e.start, e.end, e.text))
    assert result == [spans[0], spans[2]]


# --- detection across boundaries (§9.1: never silently lose a candidate) ----


def _assert_detected(masker: RuleBasedMasker, text: str, needle: str, label: str) -> None:
    entities = masker.detect(text)
    assert any(e.label == label and needle in e.text for e in entities), (
        f"{label} «{needle}» not found: {[(e.label, e.text) for e in entities]}"
    )


def test_all_entity_types_survive_window_boundaries(small_windows: Settings) -> None:
    """Person, phone, URL, org and a long address crossing a chunk boundary."""
    masker = get_masker()
    filler = "Обычный текст без персональных данных. " * 20  # > chunk size
    cases = [
        ("Иван Петров", "person"),
        ("+7 (900) 123-45-67", "phone"),
        ("https://example.com/docs/contract-99", "link"),
        ("ivan99@example.com", "email"),
        ("ООО «Росхим-99»", "organization"),
        ("г. Москва, ул. Ленина, д. 5, кв. 10", "address"),
    ]
    for needle, label in cases:
        text = f"{filler}{needle}. {filler}"
        _assert_detected(masker, text, needle, label)


def test_long_address_crossing_boundary_is_not_cut(small_windows: Settings) -> None:
    """A candidate split exactly at a window cut must be fully masked once."""
    masker = get_masker()
    head = "x" * 250  # falls inside the first window
    address = "Адрес: г. Санкт-Петербург, ул. Гагарина, д. 47, кв. 12."
    text = f"{head} {address} Хвост документа."
    entities = masker.detect(text)
    addresses = [e for e in entities if e.label == "address"]
    assert len(addresses) == 1
    assert "Гагарина" in addresses[0].text
    assert "47" in addresses[0].text
    assert text[addresses[0].start : addresses[0].end] == addresses[0].text  # absolute offsets


@pytest.mark.parametrize(
    "needle",
    [
        "Москва, улица Ленина, дом 10",
        "г Москва, ул Ленина, д 10",
        "г. Москва, ул. Ленина, д. 10, кв. 25",
    ],
)
def test_address_markers_survive_window_boundaries(small_windows: Settings, needle: str) -> None:
    """Full words and bare/dotted abbreviations must not be cut at a window."""
    masker = get_masker()
    filler = "Обычный текст без персональных данных. " * 20  # > chunk size
    text = f"{filler}{needle}. {filler}"
    entities = [entity for entity in masker.detect(text) if entity.label == "address"]
    hit = next((entity for entity in entities if "Ленина" in entity.text), None)
    assert hit is not None, f"address «{needle}» lost at a window boundary: {entities}"
    assert text[hit.start : hit.end] == hit.text  # absolute offsets


def test_windowed_offsets_are_absolute(small_windows: Settings) -> None:
    masker = get_masker()
    text = "Простой текст. " * 30 + "ivan@boundary.example.com" + " финал."
    entities = masker.detect(text)
    emails = [e for e in entities if e.label == "email"]
    assert len(emails) == 1
    assert text[emails[0].start : emails[0].end] == "ivan@boundary.example.com"


def test_checkpoint_runs_between_windows(small_windows: Settings) -> None:
    masker = get_masker()
    calls: list[int] = []
    masker.detect("word " * 200, checkpoint=lambda: calls.append(1))
    assert len(calls) > 1  # more than one window


# --- conservative prefilter (§9.2) -------------------------------------------


def test_prefilter_skips_only_impossible_detectors() -> None:
    masker = get_masker()
    # No digits / @ / :// / uppercase → no detector can match, but an
    # always_mask value must STILL be forced through.
    clean = "простой текст без данных — просто слова"
    assert masker.detect(clean, prefilter=True) == masker.detect(clean, prefilter=False)
    assert masker.detect("+7 (900) 123-45-67", prefilter=True)


def test_prefilter_does_not_suppress_always_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured always_mask literal bypasses the detector prefilter."""
    masker = get_masker()
    literal = "Росхим"
    forced = Entity("organization", literal, 0, len(literal))
    monkeypatch.setattr(masker, "_find_always_masked", lambda _text: iter([forced]))
    result = masker.detect("ничего подозрительного здесь", prefilter=True)
    assert forced in result


# --- thread-local masker (§9.2) ----------------------------------------------


def test_get_masker_is_per_thread() -> None:
    main_masker = get_masker()
    seen: list[int] = []

    def worker() -> None:
        seen.append(id(get_masker()))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen
    assert seen[0] != id(main_masker)
    assert get_masker() is main_masker  # same thread reuses its instance


def test_reset_masker_drops_only_this_thread() -> None:
    first = get_masker()
    rule_based.reset_masker()
    assert get_masker() is not first
