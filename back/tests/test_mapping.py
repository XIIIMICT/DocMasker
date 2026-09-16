"""Tests for the demasking keys (placeholders, numbering and the YAML format)."""

from __future__ import annotations

import pytest

from src.masking.mapping import MaskKeys, format_keys_yaml, parse_keys_yaml


def test_numbering_is_per_label_and_follows_detection_order() -> None:
    keys = MaskKeys()
    assert keys.placeholder("person", "Иван") == "[PERSON_1]"
    assert keys.placeholder("phone", "+7 900") == "[PHONE_1]"
    assert keys.placeholder("person", "Пётр") == "[PERSON_2]"


def test_same_value_keeps_its_number() -> None:
    keys = MaskKeys()
    first = keys.placeholder("person", "Иван")
    assert keys.placeholder("person", "Иван") == first


def test_identical_values_share_one_key() -> None:
    # The same literal string repeated is one entity: one placeholder, one key.
    keys = MaskKeys()
    assert keys.placeholder("organization", "Росхим") == "[ORGANIZATION_1]"
    assert keys.placeholder("organization", "Росхим") == "[ORGANIZATION_1]"
    assert parse_keys_yaml(keys.to_yaml()) == {"Организации": ["Росхим"]}


def test_distinct_wordings_of_one_org_are_separate_keys() -> None:
    # Different case forms are different strings and must stay separate — only
    # then can the original declension be restored.
    keys = MaskKeys()
    assert keys.placeholder("organization", 'АО "Росхим"') == "[ORGANIZATION_1]"
    assert keys.placeholder("organization", "Акционерного общества") == "[ORGANIZATION_2]"
    assert parse_keys_yaml(keys.to_yaml()) == {
        "Организации": ['АО "Росхим"', "Акционерного общества"],
    }


def test_sections_follow_label_titles_order_and_omit_empty_ones() -> None:
    keys = MaskKeys()
    keys.placeholder("organization", "А")
    keys.placeholder("person", "Б")
    keys.placeholder("phone", "В")
    # No email/address entries: those sections must be absent.
    assert keys.document() == {
        "Физлица": ["Б"],
        "Номера телефонов": ["В"],
        "Организации": ["А"],
    }


def test_yaml_is_empty_when_no_entities() -> None:
    keys = MaskKeys()
    assert keys.to_yaml() == ""
    assert keys.document() == {}


def test_format_keys_yaml_skips_empty_sections() -> None:
    assert format_keys_yaml({"Физлица": [], "Организации": ["АО Ромашка"]}) == (
        "Организации:\n  - АО Ромашка"
    )


def test_yaml_roundtrip_preserves_values() -> None:
    document = {
        "Физлица": ["Иван Петров", "Пётр Сидоров"],
        "Организации": ['АО "Росхим"', 'ООО "Ромашка"'],
    }
    assert parse_keys_yaml(format_keys_yaml(document)) == document


def test_parse_rejects_non_mapping() -> None:
    with pytest.raises(TypeError):
        parse_keys_yaml("- просто\n- список\n")


def test_parse_rejects_non_string_items() -> None:
    with pytest.raises(TypeError):
        parse_keys_yaml("Физлица:\n  - 42\n")
