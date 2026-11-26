"""Phase 7 §9.1/§9.2: windowed detection, prefilter, thread-local masker."""

import itertools
import threading

import pytest

from src.config import Settings
from src.masking import rule_based, windowed
from src.masking.rule_based import Entity, RuleBasedMasker, get_masker
from src.masking.windowed import OversizedTokenError, dedupe_boundary_spans, iter_windows

_ALIASES = {
    "detection_min_chars": "DETECTION_MIN_CHARS",
    "detection_chunk_chars": "DETECTION_CHUNK_CHARS",
    "detection_overlap_chars": "DETECTION_OVERLAP_CHARS",
    "detection_max_window_chars": "DETECTION_MAX_WINDOW_CHARS",
}

_CEILING = 4096


@pytest.fixture
def small_windows(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Force windowing on short strings so boundary tests stay fast."""
    settings = Settings.model_validate(
        {
            "DETECTION_MIN_CHARS": 64,
            "DETECTION_CHUNK_CHARS": 128,
            "DETECTION_OVERLAP_CHARS": 48,
            "DETECTION_MAX_WINDOW_CHARS": _CEILING,
        }
    )
    monkeypatch.setattr(rule_based, "get_settings", lambda: settings)
    monkeypatch.setattr(windowed, "get_settings", lambda: settings)
    return settings


# --- iter_windows ------------------------------------------------------------


def test_iter_windows_short_text_is_one_window() -> None:
    assert list(iter_windows("abc", min_chars=200, chunk_chars=64, overlap_chars=16, max_window_chars=256)) == [
        (0, "abc")
    ]


def test_iter_windows_covers_every_character_without_gaps() -> None:
    text = ("Предложение номер двадцать пять. " * 40).strip()
    windows = list(iter_windows(text, min_chars=50, chunk_chars=128, overlap_chars=32, max_window_chars=512))
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
    _offset, window = next(
        iter(iter_windows(text, min_chars=50, chunk_chars=128, overlap_chars=16, max_window_chars=512))
    )
    assert window.endswith("\n")


def _window_bounds(text: str, **kwargs: int) -> list[tuple[int, int]]:
    return [(offset, offset + len(window)) for offset, window in iter_windows(text, **kwargs)]


def _windows(text: str, **kwargs: int) -> list[tuple[int, str]]:
    return list(iter_windows(text, **kwargs))


def test_long_token_crossing_hard_cut_is_contained_whole() -> None:
    """DM-5: a token longer than the overlap must not be split at the cut."""
    token = "https://example.com/docs/" + "a" * 120  # > 48-char overlap
    text = "(" * 100 + token + " Обычный хвост документа."
    bounds = _window_bounds(text, min_chars=64, chunk_chars=128, overlap_chars=48, max_window_chars=_CEILING)
    token_start, token_end = 100, 100 + len(token)
    assert bounds[0][0] == 0
    assert bounds[0][1] > token_end  # first window extended past the whole token
    assert bounds[-1][1] == len(text)
    assert any(begin <= token_start and end >= token_end for begin, end in bounds)
    for _begin, end in bounds:
        assert not (token_start < end < token_end)  # no window ends mid-token


def test_unbroken_identifier_longer_than_overlap_is_not_cut() -> None:
    """A dense alphanumeric token (no punctuation) still gets a whole window."""
    token = "ID" + "A" * 200
    text = token + " разделитель"
    windows = _windows(text, min_chars=64, chunk_chars=128, overlap_chars=48, max_window_chars=_CEILING)
    assert any(token in window for _offset, window in windows)
    for offset, window in windows:
        end = offset + len(window)
        assert not (0 < end < len(token))  # no window stops inside the token
    assert windows[-1][0] + len(windows[-1][1]) == len(text)


def test_punctuation_and_whitespace_cases_keep_token_boundaries() -> None:
    """Structural markers, tabs and newlines are all safe cut points."""
    text = ("alpha beta gamma; delta epsilon. zeta!\nИван\tПетров; broke" * 6) + " финал."
    windows = _windows(text, min_chars=64, chunk_chars=128, overlap_chars=32, max_window_chars=1024)
    assert windows[0][0] == 0
    assert windows[-1][0] + len(windows[-1][1]) == len(text)
    for offset, window in windows:
        assert window == text[offset : offset + len(window)]
        end = offset + len(window)
        if end < len(text):
            assert text[end - 1].isspace() or text[end].isspace()


def test_short_text_bypasses_the_window_ceiling() -> None:
    """Below `min_chars` the old single-window path is unchanged (DM-5)."""
    assert _windows("x" * 1000, min_chars=2000, chunk_chars=128, overlap_chars=48, max_window_chars=256) == [
        (0, "x" * 1000)
    ]


def test_unbreakable_run_larger_than_ceiling_fails_closed() -> None:
    """No bound violation: unsupported input is rejected, never silently cut."""
    with pytest.raises(OversizedTokenError) as exc_info:
        _windows("x" * 5000, min_chars=64, chunk_chars=128, overlap_chars=48, max_window_chars=256)
    assert exc_info.value.code == "oversized_token"
    assert exc_info.value.status_code == 422
    assert "x" * 100 not in str(exc_info.value.detail)  # no input echoed


def test_long_dense_input_is_bounded_and_deterministic() -> None:
    """Synthetic dense corpus: bounded windows, full coverage, stable output."""
    token = "T" * 90  # longer than the overlap, repeated without long prose
    text = " ".join([token] * 300)  # ~27 KB
    kwargs = {"min_chars": 64, "chunk_chars": 128, "overlap_chars": 48, "max_window_chars": _CEILING}
    windows = _windows(text, **kwargs)
    assert len(windows) > 100
    assert all(len(window) <= kwargs["max_window_chars"] for _offset, window in windows)
    assert windows[0][0] == 0
    assert windows[-1][0] + len(windows[-1][1]) == len(text)
    for (offset_a, win_a), (offset_b, _win_b) in itertools.pairwise(windows):
        assert offset_b < offset_a + len(win_a)  # no silent gap
    assert windows == _windows(text, **kwargs)


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


# --- DM-5: oversized-token cases ---------------------------------------------


def test_long_url_crossing_hard_cut_is_detected_whole(small_windows: Settings) -> None:
    """The extended window carries the whole URL, re-based to absolute offsets."""
    masker = get_masker()
    token = "https://example.com/docs/" + "a" * 120  # > overlap, cut at chunk 128
    text = "(" * 100 + token + " Обычный хвост документа."
    links = [entity for entity in masker.detect(text) if entity.label == "link"]
    assert len(links) == 1
    assert links[0].text == token
    assert text[links[0].start : links[0].end] == token


def test_long_email_identifier_crossing_hard_cut_is_detected_whole(small_windows: Settings) -> None:
    """A long identifier-shaped email local part survives the chunk boundary."""
    masker = get_masker()
    token = "x" * 100 + "user_" + "a" * 120 + "@example.org"
    text = token + " Обычный хвост документа."
    emails = [entity for entity in masker.detect(text) if entity.label == "email"]
    assert len(emails) == 1
    assert emails[0].text == token
    assert (emails[0].start, emails[0].end) == (0, len(token))


def test_repeated_long_tokens_are_deduped_with_absolute_offsets(small_windows: Settings) -> None:
    """Boundary duplicates drop; each occurrence keeps its own absolute span."""
    masker = get_masker()
    token = "https://example.org/docs/" + "b" * 120
    first = "(" * 100 + token
    text = f"{first} {first} финал."
    links = [entity for entity in masker.detect(text) if entity.label == "link"]
    assert len(links) == 2
    assert [(entity.start, entity.end) for entity in links] == [
        (100, 100 + len(token)),
        (len(first) + 1 + 100, len(first) + 1 + 100 + len(token)),
    ]
    assert all(text[entity.start : entity.end] == token for entity in links)


def test_oversized_token_error_surfaces_from_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API-visible path fails closed instead of returning a half-masked entity."""
    settings = Settings.model_validate(
        {
            "DETECTION_MIN_CHARS": 64,
            "DETECTION_CHUNK_CHARS": 128,
            "DETECTION_OVERLAP_CHARS": 48,
            "DETECTION_MAX_WINDOW_CHARS": 256,
        }
    )
    monkeypatch.setattr(rule_based, "get_settings", lambda: settings)
    monkeypatch.setattr(windowed, "get_settings", lambda: settings)
    with pytest.raises(OversizedTokenError):
        get_masker().detect("x" * 5000)


def test_checkpoint_stops_at_next_window_with_long_tokens(small_windows: Settings) -> None:
    """Forward extension still checks cancellation between windows, never per char."""

    class _CancelledError(Exception):
        pass

    masker = get_masker()
    text = " ".join(["t" * 90] * 40)  # every window needs a forward extension
    calls: list[int] = []

    def checkpoint() -> None:
        calls.append(1)
        if len(calls) == 3:
            raise _CancelledError

    with pytest.raises(_CancelledError):
        masker.detect(text, checkpoint=checkpoint)
    assert len(calls) == 3  # stopped promptly at the third window


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
