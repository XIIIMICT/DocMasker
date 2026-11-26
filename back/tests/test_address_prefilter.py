"""DM-2: config-driven address candidate prefilter.

A candidate only nominates a sentence/range for `AddrExtractor`; it must never
auto-mask the whole sentence. The lexicon in `config/masking/lexicons/addresses.yaml`
is the single source of truth for every marker, so no parallel Python list is
allowed here.
"""

from dataclasses import replace

import pytest

from src.masking import rule_based
from src.masking.config import get_config
from src.masking.rule_based import (
    RuleBasedMasker,
    _address_abbreviation_dots,
    _address_candidate_ranges,
    _address_candidate_re,
)


def _masker() -> RuleBasedMasker:
    return RuleBasedMasker.for_labels(["address"])


# --- the configured lexicon is the source of truth ---------------------------


def test_full_words_come_from_the_validated_lexicon(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = get_config()
    assert "улица" in cfg.address_keywords
    assert "дом" in cfg.address_keywords
    pattern = _address_candidate_re().pattern
    assert "улица" in pattern
    assert "дом" in pattern

    # Remove the marker from the configuration and it disappears from the
    # candidate matcher — proof that no hardcoded Python list backs it up.
    narrowed = replace(cfg, address_keywords=tuple(w for w in cfg.address_keywords if w != "улица"))
    monkeypatch.setattr(rule_based, "_cfg", lambda: narrowed)
    _address_candidate_re.cache_clear()
    assert "улица" not in _address_candidate_re().pattern
    assert _address_candidate_re().search("улица Ленина") is None
    _address_candidate_re.cache_clear()


def test_abbreviations_come_from_the_validated_lexicon(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = get_config()
    assert {"г", "ул", "д", "кв"} <= cfg.address_abbreviations
    assert "г." in _address_abbreviation_dots()

    narrowed = replace(cfg, address_abbreviations=frozenset({"ул"}))
    monkeypatch.setattr(rule_based, "_cfg", lambda: narrowed)
    _address_abbreviation_dots.cache_clear()
    _address_candidate_re.cache_clear()
    assert _address_candidate_re().search("кв 25") is None
    assert _address_candidate_re().search("ул Ленина") is not None
    _address_abbreviation_dots.cache_clear()
    _address_candidate_re.cache_clear()


# --- full words, dotted and bare abbreviations: boundaries -------------------


@pytest.mark.parametrize("text", ["улица Ленина", "дом 10", "УЛИЦА ЛЕНИНА", "Дом 10"])
def test_full_word_markers_nominate(text: str) -> None:
    assert _address_candidate_re().search(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "г. Москва",
        "ул. Ленина",
        "д. 10",
        "кв. 25",
        "корп. 2",
        "р-н Дрогомилово",
    ],
)
def test_dotted_abbreviations_nominate(text: str) -> None:
    assert _address_candidate_re().search(text) is not None


@pytest.mark.parametrize(
    "text",
    ["г Москва", "ул Ленина", "д 10", "кв 25", "дом 10, корпус 2"],
)
def test_bare_abbreviations_nominate(text: str) -> None:
    assert _address_candidate_re().search(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Строймонтаж заключил договор",  # «ш»/«д» inside words
        "улице Ленина",  # inflected form is not the configured full word
        "домик в деревне",
        "Компания ш consulted",  # bare «ш» with a lowercase continuation
        "г в офисе",  # bare «г» with a lowercase continuation
        "и т.д. всё нормально",  # tail of «т.д.» must not be a marker
        "Москва, 10",  # two digits are not a postcode
        "Встреча состоялась в офисе.",
    ],
)
def test_unrelated_prose_does_not_nominate(text: str) -> None:
    assert _address_candidate_re().search(text) is None


@pytest.mark.parametrize("text", ["109012", "Москва, 109012", "Адрес: что-то", "адресу: что-то"])
def test_postcode_and_address_labels_nominate(text: str) -> None:
    assert _address_candidate_re().search(text) is not None


# --- ranges: one nomination, not an auto-mask --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Москва, улица Ленина, дом 10",
        "г Москва, ул Ленина, д 10",
        "109012, г. Москва, ул. Ленина, д. 10, корп. 2, кв. 25",
        "г Москва, ул Ленина, дом 10, корпус 2, квартира 25",
    ],
)
def test_address_sentence_is_one_candidate_range(text: str) -> None:
    ranges = list(_address_candidate_ranges(text))
    assert ranges == [(0, len(text))]


def test_no_marker_means_no_candidate_range() -> None:
    text = "Обычный текст без адресов. Встреча в офисе."
    assert list(_address_candidate_ranges(text)) == []


def test_abbreviation_dots_are_not_sentence_ends() -> None:
    text = "г. Москва, ул. Ленина, д. 10, кв. 25. Пишите нам."
    ranges = list(_address_candidate_ranges(text))
    assert ranges
    assert "кв. 25" in text[ranges[0][0] : ranges[0][1]]


def test_multiline_address_is_one_candidate_range() -> None:
    text = "Адрес: г. Москва,\nул. Ленина, д. 10.\nПишите нам."
    ranges = list(_address_candidate_ranges(text))
    assert ranges
    first = text[ranges[0][0] : ranges[0][1]]
    assert "Москва" in first
    assert "Ленина" in first
    assert "\n" in first


def test_ordinary_line_after_address_is_not_nominated() -> None:
    text = "Москва, улица Ленина, дом 10\nПишите нам."
    ranges = list(_address_candidate_ranges(text))
    assert ranges
    assert "Пишите" not in text[ranges[0][0] : ranges[0][1]]


# --- detection: nominate the range, let AddrExtractor decide ------------------


def test_brief_full_word_example_is_detected() -> None:
    text = "Москва, улица Ленина, дом 10"
    entities = [entity for entity in _masker().detect(text) if entity.label == "address"]
    assert len(entities) == 1
    assert "Ленина" in entities[0].text
    assert text[entities[0].start : entities[0].end] == entities[0].text


def test_brief_bare_abbreviation_example_is_detected() -> None:
    text = "г Москва, ул Ленина, д 10"
    entities = [entity for entity in _masker().detect(text) if entity.label == "address"]
    assert len(entities) == 1
    assert "ул Ленина" in entities[0].text
    assert text[entities[0].start : entities[0].end] == entities[0].text


def test_explicit_address_label_masks_building_corpus_and_apartment() -> None:
    address = "г. Москва, ул. Ленина, д. 10, корп. 2, кв. 25"
    assert _masker().mask(f"Адрес: {address}.") == "Адрес: [ADDRESS]."


def test_postcoded_address_masks_whole_value() -> None:
    address = "109012, г. Москва, ул. Ленина, д. 10, корп. 2, кв. 25"
    assert _masker().mask(f"Юр. адрес: {address}.") == "Юр. адрес: [ADDRESS]."


def test_multiline_address_label_masks_across_the_line_break() -> None:
    assert _masker().mask("Адрес: г. Москва,\nул. Ленина, д. 10.") == "Адрес: [ADDRESS]."


def test_candidate_does_not_auto_mask_the_whole_sentence() -> None:
    text = "Москва, улица Ленина, дом 10. Пишите нам."
    masked = _masker().mask(text)
    assert "[ADDRESS]" in masked
    assert "Пишите нам" in masked
    assert "Ленина" not in masked


def test_prose_with_full_words_nominates_but_is_not_masked() -> None:
    """Precision guard: a nomination is cheap; AddrExtractor still decides."""
    text = "Новый дом на улице — это хорошо. Половина дома в тени."
    assert list(_address_candidate_ranges(text))
    assert all(entity.label != "address" for entity in _masker().detect(text))
    assert _masker().mask(text) == text


def test_ordinary_prose_has_no_address_nomination_or_mask() -> None:
    text = "Встреча в офисе. Строймонтаж заключил договор. Компания ш consulted."
    assert list(_address_candidate_ranges(text)) == []
    assert _masker().detect(text) == []
