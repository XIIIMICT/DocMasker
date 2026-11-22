"""Tests for masking profiles (ADR-0001): validation, registry, extensibility."""

import pytest

from src.masking.constants import DEFAULT_LABELS
from src.masking.detectors import DETECTORS
from src.masking.profiles import (
    DEFAULT_PROFILE,
    LabelMeta,
    MaskingProfile,
    ProfilesValidationError,
    get_profile,
    list_profiles,
    registered_titles,
)
from src.masking.rule_based import RuleBasedMasker


def test_default_profile_reproduces_pre_profile_behavior() -> None:
    # The built-in profile must be exactly what the pipeline did before
    # profiles existed: the default label set, the legacy detector order and
    # the project always-mask list.
    assert DEFAULT_PROFILE.profile_id == "russian-default"
    assert DEFAULT_PROFILE.version == 1
    assert DEFAULT_PROFILE.label_ids() == DEFAULT_LABELS
    assert DEFAULT_PROFILE.detectors == tuple(DETECTORS)
    assert DEFAULT_PROFILE.always_mask_organizations == ("Росхим",)


def test_titles_match_the_legacy_label_titles() -> None:
    assert DEFAULT_PROFILE.titles() == {
        "person": "Физлица",
        "phone": "Номера телефонов",
        "email": "Email",
        "link": "Ссылки",
        "organization": "Организации",
        "address": "Адреса",
    }


def test_get_profile_resolves_default_and_unknown() -> None:
    assert get_profile(None) is DEFAULT_PROFILE
    assert get_profile("russian-default") is DEFAULT_PROFILE
    with pytest.raises(ProfilesValidationError, match="Неизвестный профиль"):
        get_profile("no-such-profile")


def test_list_profiles_contains_the_default() -> None:
    assert DEFAULT_PROFILE in list_profiles()


def test_duplicate_labels_are_rejected() -> None:
    labels = tuple(
        LabelMeta(id=label, title=f"Заголовок {index}", order=index) for index, label in enumerate(("person", "person"))
    )
    with pytest.raises(ProfilesValidationError, match="больше одного раза"):
        MaskingProfile(
            profile_id="broken",
            version=1,
            display_name="Broken",
            labels=labels,
            detectors=("email",),
        )


def test_duplicate_titles_are_rejected() -> None:
    labels = (
        LabelMeta(id="person", title="Один", order=0),
        LabelMeta(id="email", title="Один", order=1),
    )
    with pytest.raises(ProfilesValidationError, match="повторяется"):
        MaskingProfile(
            profile_id="broken",
            version=1,
            display_name="Broken",
            labels=labels,
            detectors=("email",),
        )


def test_unknown_detector_is_rejected() -> None:
    labels = (LabelMeta(id="person", title="Физлица", order=0),)
    with pytest.raises(ProfilesValidationError, match="неизвестные детекторы"):
        MaskingProfile(
            profile_id="broken",
            version=1,
            display_name="Broken",
            labels=labels,
            detectors=("astrology",),
        )


def test_detector_without_label_metadata_is_rejected() -> None:
    labels = (LabelMeta(id="person", title="Физлица", order=0),)
    with pytest.raises(ProfilesValidationError, match="метаданные"):
        MaskingProfile(
            profile_id="broken",
            version=1,
            display_name="Broken",
            labels=labels,
            detectors=("email",),
        )


def test_for_labels_rejects_unknown_and_duplicates() -> None:
    with pytest.raises(ProfilesValidationError, match="banana"):
        RuleBasedMasker.for_labels(["person", "banana"])
    with pytest.raises(ProfilesValidationError, match="больше одного раза"):
        RuleBasedMasker.for_labels(["person", "person"])


def test_for_labels_subset_masks_only_the_selected_labels() -> None:
    masker = RuleBasedMasker.for_labels(["email"])
    entities = masker.detect("Иван Петров, ivan@mail.ru")
    assert [entity.label for entity in entities] == ["email"]


def test_registered_titles_cover_the_default_profile() -> None:
    titles = registered_titles()
    for label, title in DEFAULT_PROFILE.titles().items():
        assert titles[label] == title
