"""Tests for the keys `Профиль` marker and the legacy envelope-less reader."""

import pytest

from src.masking.mapping import (
    KeysValidationError,
    MaskKeys,
    format_keys_yaml,
    format_keys_yaml_with_profile,
    parse_keys_yaml,
    placeholders_from_document,
    validate_keys_yaml,
)
from src.masking.profiles import DEFAULT_PROFILE


def test_mask_keys_with_profile_writes_the_profile_marker() -> None:
    keys = MaskKeys(DEFAULT_PROFILE)
    keys.placeholder("person", "Иван Петров")
    keys.placeholder("phone", "+7 900 123-45-67")

    text = keys.to_yaml()
    parsed = parse_keys_yaml(text)
    assert parsed == {
        "Физлица": ["Иван Петров"],
        "Номера телефонов": ["+7 900 123-45-67"],
    }
    assert "Профиль: russian-default" in text
    # No version keys are written.
    assert "версия" not in text
    assert "версия_профиля" not in text


def test_round_trip_restores_the_document() -> None:
    keys = MaskKeys(DEFAULT_PROFILE)
    marker = keys.placeholder("person", "Иван Петров")
    masked = f"{marker} подписал."
    restored = masked
    for placeholder, value in placeholders_from_document(validate_keys_yaml(keys.to_yaml())).items():
        restored = restored.replace(placeholder, value)
    assert restored == "Иван Петров подписал."


def test_legacy_envelope_less_file_is_still_accepted() -> None:
    legacy = format_keys_yaml({"Физлица": ["Иван Петров"], "Номера телефонов": ["+7 900 123-45-67"]})
    assert "Профиль" not in legacy  # sanity: the helper still writes the legacy shape
    placeholders = placeholders_from_document(validate_keys_yaml(legacy))
    assert placeholders == {"[PERSON_1]": "Иван Петров", "[PHONE_1]": "+7 900 123-45-67"}


def test_version_keys_are_not_read_anymore() -> None:
    # The v2 shape never shipped to prod; a file carrying version keys is
    # rejected as an unknown section rather than half-parsed.
    text = "версия: 2\nпрофиль: russian-default\nверсия_профиля: 1\nФизлица:\n  - Иван Петров"
    with pytest.raises(KeysValidationError, match="Неизвестный раздел"):
        validate_keys_yaml(text)


def test_profile_marker_does_not_count_as_a_section() -> None:
    text = format_keys_yaml_with_profile({"Физлица": ["Иван Петров"]}, "russian-default")
    assert validate_keys_yaml(text) == {"Физлица": ["Иван Петров"]}


def test_duplicate_section_still_detected_with_marker() -> None:
    text = format_keys_yaml_with_profile({"Физлица": ["Иван Петров"]}, "russian-default")
    tampered = text + "\nФизлица:\n  - Дублёр"
    with pytest.raises(KeysValidationError, match="больше одного раза"):
        validate_keys_yaml(tampered)


def test_unknown_profile_still_restores() -> None:
    # Restoring never depends on the producing profile: the marker is metadata.
    text = format_keys_yaml_with_profile({"Организации": ['АО "Росхим"']}, "future-profile")
    placeholders = placeholders_from_document(validate_keys_yaml(text))
    assert placeholders == {"[ORGANIZATION_1]": 'АО "Росхим"'}


def test_no_profile_defaults_to_the_default_profile() -> None:
    # `None` means the default profile; the marker is still written explicitly.
    text = format_keys_yaml_with_profile({"Физлица": ["Иван Петров"]}, None)
    assert "Профиль: russian-default" in text
    assert validate_keys_yaml(text) == {"Физлица": ["Иван Петров"]}


def test_empty_keys_serialize_to_an_empty_string() -> None:
    assert format_keys_yaml_with_profile({}, None) == ""
    assert MaskKeys(DEFAULT_PROFILE).to_yaml() == ""


def test_parse_rejects_non_list_sections() -> None:
    with pytest.raises(TypeError, match="списком строк"):
        parse_keys_yaml("Физлица: Иван")
