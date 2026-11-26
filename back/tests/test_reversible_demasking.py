"""DM-1: single-pass non-cascading restoration and the mask-time collision guard.

All values here are synthetic fixtures; no real personal data is used.
"""

import io

import pytest
from docx import Document as DocxDocument

from src.masking.docx_service import mask_docx_in_memory, unmask_docx_in_memory
from src.masking.mapping import (
    MarkerCollisionError,
    MaskKeys,
    parse_keys_yaml,
    placeholders_from_document,
)
from src.masking.rule_based import RuleBasedMasker
from src.masking.schemas import MaskedEntity
from src.masking.service import mask_text, unmask_text


def _entity(value: str, source: str, label: str = "person") -> MaskedEntity:
    start = source.index(value)
    return MaskedEntity(label=label, text=value, start=start, end=start + len(value), score=1.0)


def _docx(text: str) -> bytes:
    doc = DocxDocument()
    doc.add_paragraph(text)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# --- text: one scan, opaque replacements ------------------------------------


def test_unmask_value_containing_another_marker_does_not_cascade() -> None:
    placeholders = {"[PERSON_1]": "[PHONE_1]", "[PHONE_1]": "Иван"}
    assert unmask_text("[PERSON_1]", placeholders) == "[PHONE_1]"
    assert unmask_text("[PERSON_1] и [PHONE_1]", placeholders) == "[PHONE_1] и Иван"


def test_unmask_adjacent_markers_and_punctuation() -> None:
    placeholders = {"[PERSON_1]": "Иван", "[PHONE_1]": "+7 900", "[EMAIL_1]": "a@b.ru"}
    assert unmask_text("[PERSON_1][PHONE_1]! [EMAIL_1]?", placeholders) == "Иван+7 900! a@b.ru?"


def test_unmask_preserves_no_marker_duplicate_values_and_missing_markers() -> None:
    assert unmask_text("обычный текст", {"[PERSON_1]": "Иван"}) == "обычный текст"
    assert unmask_text("x [PERSON_1] y", {}) == "x [PERSON_1] y"
    placeholders = {"[PERSON_1]": "Иван", "[PERSON_2]": "Иван"}
    assert unmask_text("[PERSON_1], [PERSON_2], [PERSON_3]", placeholders) == "Иван, Иван, [PERSON_3]"


def test_unmask_compiles_longest_first() -> None:
    # Artificial prefix-shaped keys exercise the ordering independently of the
    # canonical `[LABEL_N]` markers, which cannot prefix each other.
    placeholders = {"[PERSON_1]": "A", "[PERSON_1]x": "B"}
    assert unmask_text("[PERSON_1]x", placeholders) == "B"


def test_unmask_high_occurrence_input() -> None:
    placeholders = {"[PERSON_1]": "Иван Петров"}
    text = " ".join(["[PERSON_1]"] * 2000)
    assert unmask_text(text, placeholders) == " ".join(["Иван Петров"] * 2000)


# --- text: fail-fast collision guard ----------------------------------------


def test_collision_error_is_a_clear_422() -> None:
    error = MarkerCollisionError(["[PERSON_1]"])
    assert error.status_code == 422
    assert error.code == "marker_collision"
    assert "маркер" in str(error)


def test_mask_rejects_a_literal_marker_that_will_be_allocated() -> None:
    text = "Иван и [PERSON_1]"
    with pytest.raises(MarkerCollisionError, match="маркер"):
        mask_text(text, [_entity("Иван", text)], MaskKeys())


def test_mask_rejects_several_literal_markers() -> None:
    text = "[PERSON_1] [PHONE_1] Иван +7 900 123-45-67"
    entities = [
        _entity("Иван", text),
        _entity("+7 900 123-45-67", text, label="phone"),
    ]
    with pytest.raises(MarkerCollisionError) as raised:
        mask_text(text, entities, MaskKeys())
    detail = str(raised.value)
    assert "[PERSON_1]" in detail
    assert "[PHONE_1]" in detail


def test_mask_accepts_a_marker_shaped_literal_that_is_not_allocated() -> None:
    text = "Иван и [PERSON_9]"
    assert mask_text(text, [_entity("Иван", text)], MaskKeys()) == "[PERSON_1] и [PERSON_9]"


# --- keys: legacy reading and unchanged format ------------------------------


def test_legacy_keys_without_the_profile_marker_still_restore() -> None:
    legacy = "Физлица:\n  - Иван Петров\nОрганизации:\n  - АО «Росхим»"
    placeholders = placeholders_from_document(parse_keys_yaml(legacy))
    assert placeholders == {"[PERSON_1]": "Иван Петров", "[ORGANIZATION_1]": "АО «Росхим»"}
    assert unmask_text("[PERSON_1] в [ORGANIZATION_1]", placeholders) == "Иван Петров в АО «Росхим»"


def test_keys_format_is_unchanged_by_the_collision_guard() -> None:
    keys = MaskKeys()
    keys.placeholder("person", "Иван")
    assert keys.to_yaml() == "Профиль: russian-default\nФизлица:\n  - Иван"
    assert "версия" not in keys.to_yaml().lower()


# --- DOCX: container-local non-cascading restore ----------------------------


def test_unmask_docx_value_with_a_marker_does_not_cascade() -> None:
    restored, unused, _coverage = unmask_docx_in_memory(
        _docx("[PERSON_1] пришёл."),
        {"[PERSON_1]": "[PHONE_1]", "[PHONE_1]": "Иван"},
    )
    assert DocxDocument(io.BytesIO(restored)).paragraphs[0].text == "[PHONE_1] пришёл."
    assert unused == ["Иван"]


def test_unmask_docx_marker_split_across_runs_does_not_cascade() -> None:
    doc = DocxDocument()
    paragraph = doc.add_paragraph()
    paragraph.add_run("[PER")
    paragraph.add_run("SON_1]!")
    out = io.BytesIO()
    doc.save(out)

    restored, unused, _coverage = unmask_docx_in_memory(out.getvalue(), {"[PERSON_1]": "[PHONE_1]"})
    assert DocxDocument(io.BytesIO(restored)).paragraphs[0].text == "[PHONE_1]!"
    assert unused == []


def test_mask_docx_rejects_a_source_marker_collision() -> None:
    with pytest.raises(MarkerCollisionError, match="маркер"):
        mask_docx_in_memory(_docx("Иван Петров и [PERSON_1]"), RuleBasedMasker.for_labels(["person"]))


def test_mask_docx_rejects_a_collision_in_another_part() -> None:
    doc = DocxDocument()
    doc.add_paragraph("Иван Петров")
    doc.sections[0].header.paragraphs[0].add_run("Ссылка [PERSON_1]")
    out = io.BytesIO()
    doc.save(out)

    with pytest.raises(MarkerCollisionError):
        mask_docx_in_memory(out.getvalue(), RuleBasedMasker.for_labels(["person"]))


def test_mask_docx_accepts_an_unallocated_marker_shaped_literal() -> None:
    masked, entities, keys, _coverage = mask_docx_in_memory(
        _docx("Иван Петров и [PERSON_9]"), RuleBasedMasker.for_labels(["person"])
    )
    assert DocxDocument(io.BytesIO(masked)).paragraphs[0].text == "[PERSON_1] и [PERSON_9]"
    assert [entity.label for entity in entities] == ["person"]
    assert parse_keys_yaml(keys.to_yaml()) == {"Физлица": ["Иван Петров"]}
