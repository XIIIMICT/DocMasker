"""DM-4: one-pass, fail-closed output construction in `mask_text`.

All values here are synthetic fixtures; no real personal data is used.
"""

import platform
import sys
import time

import pytest

from src.masking.mapping import MaskKeys
from src.masking.schemas import MaskedEntity
from src.masking.service import InvalidSpanError, _mask_parts, mask_text, unmask_text


def _span_of(source: str, value: str, label: str = "person") -> MaskedEntity:
    start = source.index(value)
    return MaskedEntity(label=label, text=value, start=start, end=start + len(value), score=1.0)


def _dense_input(count: int) -> tuple[str, list[MaskedEntity]]:
    chunks: list[str] = []
    entities: list[MaskedEntity] = []
    cursor = 0
    for index in range(count):
        if index:
            chunks.append(" ")
            cursor += 1
        value = "Иван"
        chunks.append(value)
        entities.append(MaskedEntity(label="person", text=value, start=cursor, end=cursor + len(value), score=1.0))
        cursor += len(value)
    return "".join(chunks), entities


class _SliceSpy:
    """Count the source slices and characters requested by the renderer."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.calls = 0
        self.chars = 0

    def __len__(self) -> int:
        return len(self.value)

    def __getitem__(self, key: slice) -> str:
        piece = self.value[key]
        self.calls += 1
        self.chars += len(piece)
        return piece


# --- one-pass output ---------------------------------------------------------


def test_adjacent_spans_replace_both_without_a_gap() -> None:
    text = "ИванПётр"
    entities = [_span_of(text, "Иван"), _span_of(text, "Пётр")]
    keys = MaskKeys()
    assert mask_text(text, entities, keys) == "[PERSON_1][PERSON_2]"
    assert keys.document() == {"Физлица": ["Иван", "Пётр"]}


def test_repeated_identical_values_share_one_placeholder_and_key() -> None:
    text = "Иван и Иван"
    entities = [
        MaskedEntity(label="person", text="Иван", start=0, end=4, score=1.0),
        MaskedEntity(label="person", text="Иван", start=7, end=11, score=1.0),
    ]
    keys = MaskKeys()
    assert mask_text(text, entities, keys) == "[PERSON_1] и [PERSON_1]"
    assert keys.document() == {"Физлица": ["Иван"]}


def test_punctuation_and_untouched_slices_survive() -> None:
    text = "Иван, Пётр! ivan@mail.ru?"
    entities = [
        _span_of(text, "Иван"),
        _span_of(text, "Пётр"),
        _span_of(text, "ivan@mail.ru", "email"),
    ]
    masked = mask_text(text, entities, MaskKeys())
    assert masked == "[PERSON_1], [PERSON_2]! [EMAIL_1]?"


def test_unicode_offsets_are_code_point_offsets_and_round_trip() -> None:
    text = "東京: Иван Петров! 脊柱"
    entities = [
        _span_of(text, "東京", "organization"),
        _span_of(text, "Иван Петров"),
        _span_of(text, "脊柱", "organization"),
    ]
    keys = MaskKeys()
    masked = mask_text(text, entities, keys)
    assert masked == "[ORGANIZATION_1]: [PERSON_1]! [ORGANIZATION_2]"
    assert unmask_text(masked, keys.placeholders()) == text


def test_unsorted_entities_are_numbered_in_document_order() -> None:
    text = "Иван и Пётр"
    entities = [_span_of(text, "Пётр"), _span_of(text, "Иван")]
    keys = MaskKeys()
    assert mask_text(text, entities, keys) == "[PERSON_1] и [PERSON_2]"
    assert keys.document() == {"Физлица": ["Иван", "Пётр"]}


def test_parts_keep_leading_and_trailing_slices() -> None:
    text = "A Иван B"
    assert _mask_parts(text, [(2, 6, "[PERSON_1]")]) == ["A ", "[PERSON_1]", " B"]


# --- fail-closed validation --------------------------------------------------


@pytest.mark.parametrize(
    ("entity", "reason"),
    [
        (MaskedEntity(label="person", text="Ив", start=-1, end=1, score=1.0), "bounds"),
        (MaskedEntity(label="person", text="Иван", start=0, end=12, score=1.0), "bounds"),
        (MaskedEntity(label="person", text="Ив", start=3, end=1, score=1.0), "bounds"),
        (MaskedEntity(label="person", text="", start=4, end=4, score=1.0), "empty"),
        (MaskedEntity.model_construct(label="person", text="Иван", start=1.5, end=3, score=1.0), "type"),
        (MaskedEntity.model_construct(label="person", text="Иван", start=True, end=3, score=1.0), "type"),
        (MaskedEntity(label="person", text="Пётр", start=0, end=4, score=1.0), "mismatch"),
    ],
)
def test_malformed_spans_fail_closed_before_allocating_keys(entity: MaskedEntity, reason: str) -> None:
    text = "Иван и Пётр"
    keys = MaskKeys()
    with pytest.raises(InvalidSpanError) as raised:
        mask_text(text, [entity], keys)
    assert raised.value.reason == reason
    assert raised.value.code == "invalid_span"
    assert raised.value.status_code == 422
    assert keys.document() == {}
    # The detail is fixed Russian text: no entity value is echoed back.
    assert "Иван" not in str(raised.value)
    assert "Пётр" not in str(raised.value)


@pytest.mark.parametrize(
    "pairs",
    [
        ((0, 4), (3, 8)),
        ((0, 8), (2, 4)),
        ((0, 4), (0, 4)),
    ],
)
def test_overlapping_spans_fail_closed(pairs: tuple[tuple[int, int], tuple[int, int]]) -> None:
    text = "Иван Петров и Пётр Сидоров"
    entities = [
        MaskedEntity(label="person", text=text[start:end], start=start, end=end, score=1.0) for start, end in pairs
    ]
    with pytest.raises(InvalidSpanError) as raised:
        mask_text(text, entities, MaskKeys())
    assert raised.value.reason == "overlap"


def test_empty_text_without_entities_returns_empty() -> None:
    assert mask_text("", [], MaskKeys()) == ""


def test_empty_text_with_a_zero_length_span_fails_closed() -> None:
    entity = MaskedEntity(label="person", text="", start=0, end=0, score=1.0)
    with pytest.raises(InvalidSpanError) as raised:
        mask_text("", [entity], MaskKeys())
    assert raised.value.reason == "empty"


# --- density and complexity --------------------------------------------------


def test_large_dense_synthetic_input_masks_every_occurrence() -> None:
    text, entities = _dense_input(5000)
    keys = MaskKeys()
    assert mask_text(text, entities, keys) == " ".join(["[PERSON_1]"] * 5000)
    assert keys.document() == {"Физлица": ["Иван"]}


def test_output_construction_scales_with_length_and_span_count() -> None:
    """Structural linearity is asserted; wall times are recorded, not gated."""
    print(f"env: {platform.platform()} | Python {sys.version.split()[0]}")  # noqa: T201
    for count in (250, 500, 1000, 2000, 4000):
        text, entities = _dense_input(count)
        spy = _SliceSpy(text)
        parts = _mask_parts(spy, [(entity.start, entity.end, "[PERSON_1]") for entity in entities])
        assert "".join(parts) == " ".join(["[PERSON_1]"] * count)
        # Each source character is read at most once, with at most one slice
        # per gap: O(len(text) + spans), never O(spans * len(text)).
        assert spy.calls <= count + 1
        assert spy.chars <= len(text)

        timings: list[float] = []
        for _ in range(3):
            started = time.perf_counter()
            masked = mask_text(text, entities, MaskKeys())
            timings.append(time.perf_counter() - started)
        assert masked == " ".join(["[PERSON_1]"] * count)
        print(  # noqa: T201
            f"spans={count:5d} chars={len(text):6d} "
            f"best={min(timings) * 1000:8.3f} ms slices={spy.calls:5d} src_chars={spy.chars:6d}"
        )
