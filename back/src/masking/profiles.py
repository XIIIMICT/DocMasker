"""
Masking profiles: typed, versioned configuration for the masking pipeline.

A profile states *what* is masked (label metadata + detector selection) and
*which exceptions* apply (always-masked organizations), without touching the
detection code. The built-in `russian-default` profile reproduces the
pre-profile behavior exactly. Profiles are immutable and validated on
construction — an invalid profile is a startup error, never a silent
degradation (see ADR-0001).
"""

from dataclasses import dataclass

from .constants import ALWAYS_MASK_ORGANIZATIONS, DEFAULT_LABELS
from .detectors import DETECTORS


class ProfilesValidationError(ValueError):
    """A masking profile is not internally consistent."""


@dataclass(frozen=True, slots=True)
class LabelMeta:
    """Machine id, Russian keys-section title and display order of one label."""

    id: str
    title: str
    order: int


@dataclass(frozen=True, slots=True)
class MaskingProfile:
    """
    One versioned masking configuration.

    `labels` carries the metadata (id/title/order) for every entity type the
    profile can report; `detectors` lists detector ids in run order and must be
    a subset of the detector registry (see `detectors.py`); labels referenced
    only through `always_mask_organizations` must still be declared in
    `labels`, so a keys file can name their section.
    """

    profile_id: str
    version: int
    display_name: str
    labels: tuple[LabelMeta, ...]
    detectors: tuple[str, ...]
    always_mask_organizations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id or not self.display_name:
            msg = "Профиль маскирования должен иметь идентификатор и название."
            raise ProfilesValidationError(msg)
        if self.version < 1:
            msg = "Версия профиля должна быть положительным числом."
            raise ProfilesValidationError(msg)
        if not self.labels:
            msg = f"Профиль «{self.profile_id}» не содержит ни одного типа сущности."
            raise ProfilesValidationError(msg)

        ids: set[str] = set()
        titles: set[str] = set()
        for meta in self.labels:
            if not meta.id or not meta.title:
                msg = f"Профиль «{self.profile_id}»: тип сущности должен иметь идентификатор и название."
                raise ProfilesValidationError(msg)
            if meta.id in ids:
                msg = f"Профиль «{self.profile_id}»: тип сущности «{meta.id}» указан больше одного раза."
                raise ProfilesValidationError(msg)
            if meta.title in titles:
                msg = f"Профиль «{self.profile_id}»: название раздела «{meta.title}» повторяется."
                raise ProfilesValidationError(msg)
            ids.add(meta.id)
            titles.add(meta.title)

        unknown = [detector_id for detector_id in self.detectors if detector_id not in DETECTORS]
        if unknown:
            known = ", ".join(DETECTORS)
            msg = f"Профиль «{self.profile_id}»: неизвестные детекторы: {', '.join(unknown)}. Допустимо: {known}."
            raise ProfilesValidationError(msg)
        for detector_id in self.detectors:
            detector_label = DETECTORS[detector_id].label
            if detector_label not in ids:
                msg = (
                    f"Профиль «{self.profile_id}»: детектор «{detector_id}» сообщает тип "
                    f"«{detector_label}», но метаданные для него не объявлены."
                )
                raise ProfilesValidationError(msg)

        if self.always_mask_organizations and "organization" not in ids:
            msg = f"Профиль «{self.profile_id}» использует always-mask без метаданных «organization»."
            raise ProfilesValidationError(msg)

    def label_ids(self) -> tuple[str, ...]:
        """Machine label ids in profile (display) order."""
        return tuple(meta.id for meta in self.labels)

    def titles(self) -> dict[str, str]:
        """Label id → Russian keys-section title, in profile order."""
        return {meta.id: meta.title for meta in self.labels}

    def enables(self, label: str) -> bool:
        """Whether the label is declared in this profile."""
        return any(meta.id == label for meta in self.labels)


def _default_labels() -> tuple[LabelMeta, ...]:
    """Build the pre-profile label set, preserving `LABEL_TITLES` order exactly."""
    titles: dict[str, str] = {
        "person": "Физлица",
        "phone": "Номера телефонов",
        "email": "Email",
        "link": "Ссылки",
        "organization": "Организации",
        "address": "Адреса",
    }
    return tuple(LabelMeta(id=label, title=titles[label], order=order) for order, label in enumerate(DEFAULT_LABELS))


#: The built-in profile reproducing the pre-profile pipeline behavior.
DEFAULT_PROFILE = MaskingProfile(
    profile_id="russian-default",
    version=1,
    display_name="Русский по умолчанию",
    labels=_default_labels(),
    detectors=("email", "link", "phone", "person", "address", "organization"),
    always_mask_organizations=ALWAYS_MASK_ORGANIZATIONS,
)

#: All built-in profiles, keyed by id. New profiles register here; one entry is
#: the whole change a new profile needs.
PROFILES: dict[str, MaskingProfile] = {
    DEFAULT_PROFILE.profile_id: DEFAULT_PROFILE,
}


def get_profile(profile_id: str | None) -> MaskingProfile:
    """Resolve a profile by id (`None` → the default); unknown ids are errors."""
    if profile_id is None:
        return DEFAULT_PROFILE
    profile = PROFILES.get(profile_id)
    if profile is None:
        known = ", ".join(PROFILES)
        msg = f"Неизвестный профиль маскирования: «{profile_id}». Доступны: {known}."
        raise ProfilesValidationError(msg)
    return profile


def list_profiles() -> tuple[MaskingProfile, ...]:
    """All registered profiles, registry order."""
    return tuple(PROFILES.values())


def registered_titles() -> dict[str, str]:
    """
    Label id → keys-section title across every registered profile.

    The keys format is profile-independent (restoring must not require the
    producing profile), so section titles resolve against this union.
    """
    titles: dict[str, str] = {}
    for profile in PROFILES.values():
        for meta in profile.labels:
            titles.setdefault(meta.id, meta.title)
    return titles
