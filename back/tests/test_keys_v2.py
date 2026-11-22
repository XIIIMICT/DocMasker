"""Tests for the keys v2 envelope and the legacy v1 reader (ADR-0001)."""

import pytest

from src.masking.mapping import (
    KeysValidationError,
    MaskKeys,
    format_keys_yaml,
    format_keys_yaml_v2,
    parse_keys_yaml,
    placeholders_from_document,
    validate_keys_yaml,
)
from src.masking.profiles import DEFAULT_PROFILE


def test_mask_keys_with_profile_writes_the_v2_envelope() -> None:
    keys = MaskKeys(DEFAULT_PROFILE)
    keys.placeholder("person", "Иван Петров")
    keys.placeholder("phone", "+7 900 123-45-67")

    text = keys.to_yaml()
    parsed = parse_keys_yaml(text)
    assert parsed == {
        "Физлица": ["Иван Петров"],
        "Номера телефонов": ["+7 900 123-45-67"],
    }
    assert "версия: 2" in text
    assert "профиль: russian-default" in text
    assert "версия_профиля: 1" in text


def test_v2_round_trip_restores_the_document() -> None:
    keys = MaskKeys(DEFAULT_PROFILE)
    marker = keys.placeholder("person", "Иван Петров")
    masked = f"{marker} подписал."
    restored = masked
    for placeholder, value in placeholders_from_document(validate_keys_yaml(keys.to_yaml())).items():
        restored = restored.replace(placeholder, value)
    assert restored == "Иван Петров подписал."


def test_legacy_v1_file_is_still_accepted() -> None:
    v1 = format_keys_yaml({"Физлица": ["Иван Петров"], "Номера телефонов": ["+7 900 123-45-67"]})
    assert "версия" not in v1  # sanity: the helper still writes the legacy shape
    placeholders = placeholders_from_document(validate_keys_yaml(v1))
    assert placeholders == {"[PERSON_1]": "Иван Петров", "[PHONE_1]": "+7 900 123-45-67"}


def test_future_envelope_version_is_rejected() -> None:
    text = format_keys_yaml_v2({"Физлица": ["Иван Петров"]}, "russian-default", 1)
    tampered = text.replace("версия: 2", "версия: 3")
    with pytest.raises(KeysValidationError, match="Неподдерживаемая версия"):
        validate_keys_yaml(tampered)


def test_envelope_keys_do_not_count_as_sections() -> None:
    text = format_keys_yaml_v2({"Физлица": ["Иван Петров"]}, "russian-default", 1)
    # Validation must succeed with only one real section, not treat the
    # envelope keys as unknown/empty sections.
    assert validate_keys_yaml(text) == {"Физлица": ["Иван Петров"]}


def test_duplicate_section_still_detected_with_envelope() -> None:
    text = format_keys_yaml_v2({"Физлица": ["Иван Петров"]}, "russian-default", 1)
    # Append a bare duplicate of the same section: the raw-line detector must
    # still catch it (the envelope must not hide duplicate sections).
    tampered = text + "\nФизлица:\n  - Дублёр"
    with pytest.raises(KeysValidationError, match="больше одного раза"):
        validate_keys_yaml(tampered)


def test_v2_from_an_unknown_profile_still_restores() -> None:
    # Restoring never depends on the producing profile: the envelope is metadata.
    text = format_keys_yaml_v2({"Организации": ['АО "Росхим"']}, "future-profile", 7)
    placeholders = placeholders_from_document(validate_keys_yaml(text))
    assert placeholders == {"[ORGANIZATION_1]": 'АО "Росхим"'}


def test_empty_keys_serialize_to_an_empty_string() -> None:
    assert format_keys_yaml_v2({}, None, None) == ""
    assert MaskKeys(DEFAULT_PROFILE).to_yaml() == ""


def test_parse_rejects_non_list_sections() -> None:
    with pytest.raises(TypeError, match="списком строк"):
        parse_keys_yaml("Физлица: Иван")
