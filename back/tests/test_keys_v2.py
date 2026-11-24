"""Tests for the legacy `Профиль` marker and envelope-less keys files (§5.2)."""

import pytest

from src.masking.mapping import (
    KeysValidationError,
    MaskKeys,
    format_keys_yaml,
    format_keys_yaml_with_marker,
    parse_keys_yaml,
    placeholders_from_document,
    validate_keys_yaml,
)


def test_mask_keys_writes_the_legacy_profile_marker() -> None:
    keys = MaskKeys()
    keys.placeholder("person", "Иван Петров")
    keys.placeholder("phone", "+7 900 123-45-67")

    text = keys.to_yaml()
    parsed = parse_keys_yaml(text)
    assert parsed == {
        "Физлица": ["Иван Петров"],
        "Номера телефонов": ["+7 900 123-45-67"],
    }
    assert "Профиль: russian-default" in text
    assert "версия" not in text
    assert "версия_профиля" not in text


def test_round_trip_restores_the_document() -> None:
    keys = MaskKeys()
    marker = keys.placeholder("person", "Иван Петров")
    masked = f"{marker} подписал."
    restored = masked
    for placeholder, value in placeholders_from_document(validate_keys_yaml(keys.to_yaml())).items():
        restored = restored.replace(placeholder, value)
    assert restored == "Иван Петров подписал."


def test_legacy_envelope_less_file_is_still_accepted() -> None:
    legacy = format_keys_yaml({"Физлица": ["Иван Петров"], "Номера телефонов": ["+7 900 123-45-67"]})
    assert "Профиль" not in legacy
    placeholders = placeholders_from_document(validate_keys_yaml(legacy))
    assert placeholders == {"[PERSON_1]": "Иван Петров", "[PHONE_1]": "+7 900 123-45-67"}


def test_version_keys_are_not_read_anymore() -> None:
    text = "версия: 2\nпрофиль: russian-default\nверсия_профиля: 1\nФизлица:\n  - Иван Петров"
    with pytest.raises(KeysValidationError, match="Неизвестный раздел"):
        validate_keys_yaml(text)


def test_profile_marker_does_not_count_as_a_section() -> None:
    text = format_keys_yaml_with_marker({"Физлица": ["Иван Петров"]})
    assert validate_keys_yaml(text) == {"Физлица": ["Иван Петров"]}


def test_duplicate_section_still_detected_with_marker() -> None:
    text = format_keys_yaml_with_marker({"Физлица": ["Иван Петров"]})
    tampered = text + "\nФизлица:\n  - Дублёр"
    with pytest.raises(KeysValidationError, match="больше одного раза"):
        validate_keys_yaml(tampered)


def test_foreign_profile_marker_is_ignored() -> None:
    # §5.2: there is one runtime config; any legacy profile marker is metadata.
    text = 'Профиль: future-profile\nОрганизации:\n  - АО "Росхим"'
    placeholders = placeholders_from_document(validate_keys_yaml(text))
    assert placeholders == {"[ORGANIZATION_1]": 'АО "Росхим"'}


def test_empty_keys_serialize_to_an_empty_string() -> None:
    assert format_keys_yaml_with_marker({}) == ""
    assert MaskKeys().to_yaml() == ""


def test_parse_rejects_non_list_sections() -> None:
    with pytest.raises(TypeError, match="списком строк"):
        parse_keys_yaml("Физлица: Иван")
