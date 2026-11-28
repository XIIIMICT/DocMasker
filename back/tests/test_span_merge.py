"""Span-merge policy, empty-input boundary and address-prefilter agreement tests.

Split out of `test_rule_based.py` so the detector corpus and the merge/prefilter
contract can be run (and read) independently. Test bodies are unchanged.
"""

import pytest
from rule_based_fixtures import _entity, _masker

from src.masking.rule_based import _cheap_prefilter, _merge_spans


def test_merge_keeps_identical_spans_once() -> None:
    merged = _merge_spans([_entity("person", 0, 5), _entity("person", 0, 5)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 5)]


def test_merge_same_start_prefers_the_longest_span() -> None:
    merged = _merge_spans([_entity("phone", 0, 5), _entity("address", 0, 12)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 12)]


def test_merge_drops_spans_contained_in_a_kept_span() -> None:
    merged = _merge_spans([_entity("person", 0, 10), _entity("phone", 2, 5)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 10)]


def test_merge_partial_overlap_keeps_the_earliest_start() -> None:
    merged = _merge_spans([_entity("person", 0, 8), _entity("organization", 4, 12)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 8)]


def test_merge_touching_spans_are_both_kept() -> None:
    merged = _merge_spans([_entity("person", 0, 5), _entity("phone", 5, 10)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 5), (5, 10)]


def test_merge_full_overlap_tie_keeps_the_earliest_detector() -> None:
    # Equal (start, length, score): the stable sort keeps the first detector's span.
    merged = _merge_spans([_entity("person", 0, 5), _entity("organization", 0, 5)])
    assert [entity.label for entity in merged] == ["person"]


def test_merge_long_overlapping_span_loses_to_earlier_starts() -> None:
    # A long span starting later must not swallow earlier, smaller spans.
    entities = [_entity("person", index, index + 3) for index in range(0, 2000, 2)]
    entities.append(_entity("organization", 1, 1999))
    merged = _merge_spans(entities)
    assert [entity.start for entity in merged] == sorted(entity.start for entity in merged)
    assert merged[0].label == "person"
    assert all(entity.label == "person" for entity in merged[1:])


def test_empty_text_produces_no_entities() -> None:
    assert _masker().detect("") == []


# --- P1.4: address prefilter uses the validated lexicon -----------------------


@pytest.mark.parametrize(
    "text",
    [
        "г. Москва, ул. Ленина",
        "ул. Ленина",
        "пер. Смирнова",
        "наб. Реки",
        "Москва, 109012",
        "улица Ленина",  # full-word marker, no digits
        "г Москва",  # bare abbreviation, no digits
    ],
)
def test_address_prefilter_admits_marker_fragments_without_digits(text: str) -> None:
    assert "address" in _cheap_prefilter(text)


@pytest.mark.parametrize(
    "text",
    ["Строймонтаж заключил договор", "Компания ш consulted", "Встреча состоялась в офисе."],
)
def test_address_prefilter_rejects_unrelated_prose(text: str) -> None:
    assert "address" not in _cheap_prefilter(text)


@pytest.mark.parametrize(
    "text",
    [
        "г. Москва, ул. Ленина",
        "ул. Ленина",
        "пер. Смирнова",
        "г. Москва, ул. Ленина, д. 12",
        "Москва, улица Ленина, дом 10",
        "г Москва, ул Ленина, д 10",
        "Встреча состоялась в офисе.",
        "Иван Петров, ivan@mail.ru",
    ],
)
def test_prefilter_and_full_detection_agree_on_supported_fixtures(text: str) -> None:
    masker = _masker()
    prefiltered = [(entity.label, entity.text) for entity in masker.detect(text, prefilter=True)]
    full = [(entity.label, entity.text) for entity in masker.detect(text, prefilter=False)]
    assert prefiltered == full
