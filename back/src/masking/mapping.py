"""
Demasking keys: a numbered placeholder per masked entity, and its YAML form.

Masking is reversible: every distinct entity occurrence gets its own placeholder
``[LABEL_N]`` (the counter is per label, in document order), and the original
value is recorded in a keys file. Restoring the document is then a plain
placeholder→value substitution, so declension forms are preserved exactly —
which is why two spellings of the same organisation are **two** entries, not
one.

Two file formats:

- **current** — one profile marker followed by the Russian sections:

  ```yaml
  Профиль: russian-default
  Физлица:
    - Иван Петров
  ```

- **legacy (envelope-less)** — sections only; parsed exactly as before, and
  treated as produced by the default profile.

The profile marker is metadata only: restoring resolves section titles against
the profile-independent `registered_titles()` union, so a file without the
marker (or from a different profile) still restores. Version/profile-version
keys are intentionally not written or read (the format never shipped to prod).

Sections with no entities are omitted. Restoring never needs the producing
profile.
"""

import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

import yaml12

from .constants import mask_marker
from .profiles import DEFAULT_PROFILE, registered_titles

#: Id written into the `Профиль` marker when no profile was supplied.
DEFAULT_PROFILE_ID = DEFAULT_PROFILE.profile_id

if TYPE_CHECKING:
    from .profiles import MaskingProfile

#: A top-level section header line (`Физлица:`), used to spot duplicate sections.
_SECTION_RE = re.compile(r"^([^\s#][^:]*):\s*$")

#: The single metadata key written into a keys file. A file without it is
#: legacy/envelope-less and restores as the default profile. Version and
#: profile-version keys are deliberately unsupported (never shipped).
_ENVELOPE_PROFILE_KEY = "Профиль"
_ENVELOPE_KEYS = (_ENVELOPE_PROFILE_KEY,)

#: Value of a single keys section (a list of original strings).
KeysSection = list[str]
#: The whole keys document: Russian title -> ordered values.
KeysDocument = dict[str, KeysSection]

#: Reverse of the registered section titles: a Russian title back to its label.
TITLE_LABELS: dict[str, str] = {title: label for label, title in registered_titles().items()}


class MaskKeys:
    """
    Placeholders for masked entities, numbered per label in detection order.

    Entities are keyed by their exact text: the **same** string always reuses
    the same placeholder and yields a single key entry, so a phone repeated in a
    document is masked as one ``[PHONE_1]`` and restored from one key. Distinct
    wordings stay separate — `АО «Росхим»` and `Акционерного общества «Росхим»`
    are two keys, which is what preserves the original case forms on restoring.

    The YAML writer emits one `Профиль` marker from the profile the masker ran
    with (default `russian-default` when no profile is supplied; ADR-0001).
    """

    def __init__(self, profile: MaskingProfile | None = None) -> None:
        self._profile = profile
        self._counters: dict[str, int] = {}
        self._numbers: dict[tuple[str, str], int] = {}

    def number(self, label: str, text: str) -> int:
        """Return the 1-based number for this value, allocating if needed."""
        key = (label, text)
        existing = self._numbers.get(key)
        if existing is not None:
            return existing
        self._counters[label] = self._counters.get(label, 0) + 1
        number = self._counters[label]
        self._numbers[key] = number
        return number

    def placeholder(self, label: str, text: str) -> str:
        """Return the full ``[LABEL_N]`` marker for a value."""
        return mask_marker(label, self.number(label, text))

    def document(self) -> KeysDocument:
        """
        Return the keys as a title→values mapping, in profile-title order.

        Empty sections are omitted; the values are ordered by their number.
        Titles come from the attached profile when there is one, else from the
        registered union — the format stays profile-independent.
        """
        by_label: dict[str, list[tuple[int, str]]] = {}
        for (label, text), number in self._numbers.items():
            by_label.setdefault(label, []).append((number, text))

        titles = self._profile.titles() if self._profile is not None else registered_titles()
        document: KeysDocument = {}
        for label, title in titles.items():
            entries = sorted(by_label.get(label, []))
            if entries:
                document[title] = [value for _number, value in entries]
        return document

    def to_yaml(self) -> str:
        """Render the keys with a `Профиль` marker (empty string when there are none)."""
        return format_keys_yaml_with_profile(
            self.document(),
            profile_id=self._profile.profile_id if self._profile is not None else None,
        )

    def placeholders(self) -> dict[str, str]:
        """Placeholder→original map, e.g. ``{"[PERSON_1]": "Иван Петров"}``."""
        return {mask_marker(label, number): text for (label, text), number in self._numbers.items()}


def format_keys_yaml(document: Mapping[str, Iterable[str]]) -> str:
    """Serialize a keys document to YAML (legacy v1 shape), or `""` when empty."""
    sections: dict[str, list[str]] = {}
    for title, values in document.items():
        materialized = list(values)
        if materialized:
            sections[title] = materialized
    if not sections:
        return ""
    return yaml12.format_yaml(sections)


def format_keys_yaml_with_profile(
    document: Mapping[str, Iterable[str]],
    profile_id: str | None,
) -> str:
    """
    Serialize a keys document with a `Профиль` marker, or `""` when empty.

    `None` means the default profile (restore never needs the producing
    profile, so an omitted marker is equivalent). No version keys are written
    or read: the format defines one current shape and acting on the latest is
    the rule. The marker is metadata, never the restore authority.
    """
    sections = format_keys_yaml(document)
    if not sections:
        return ""
    marker: dict[str, object] = {_ENVELOPE_PROFILE_KEY: profile_id or DEFAULT_PROFILE_ID}
    parsed_sections = yaml12.parse_yaml(sections)
    merged: dict[str, object] = dict(marker)
    if isinstance(parsed_sections, dict):
        for key, value in parsed_sections.items():
            merged[str(key)] = value
    return yaml12.format_yaml(merged)


def parse_keys_yaml(text: str) -> KeysDocument:
    """
    Parse a keys document with a `Профиль` marker or sections only.

    Raises `TypeError`/`ValueError` on malformed input. The marker is stripped
    before section parsing; any other unknown top-level key is rejected by
    `validate_keys_yaml`.
    """
    parsed = yaml12.parse_yaml(text)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        msg = "Файл ключей должен содержать словарь списков."
        raise TypeError(msg)

    working = dict(parsed)
    for key in _ENVELOPE_KEYS:
        working.pop(key, None)

    document: KeysDocument = {}
    for title, values in working.items():
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            msg = "Каждый раздел ключей должен быть списком строк."
            raise TypeError(msg)
        document[str(title)] = [value for value in values if isinstance(value, str)]
    return document


def placeholders_from_document(document: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """
    Build the placeholder→original map from a parsed keys document.

    Numbers restart at 1 within each section (that is how `MaskKeys` writes
    them), so the placeholder for the n-th value of a section is
    ``[LABEL_n]`` with the label recovered from the Russian title. Unknown
    titles are skipped so a foreign or newer keys file does not crash restoring.
    """
    mapping: dict[str, str] = {}
    for title, values in document.items():
        label = TITLE_LABELS.get(title)
        if label is None:
            continue
        for index, value in enumerate(values, start=1):
            mapping[mask_marker(label, index)] = value
    return mapping


class KeysValidationError(ValueError):
    """The demasking keys are not a valid keys document."""


def validate_keys_yaml(text: str) -> KeysDocument:
    """
    Parse and validate a demasking keys file, with a Russian error message.

    A valid file is a mapping whose keys are known Russian section titles and
    whose values are non-empty lists of non-empty strings; the `Профиль` marker
    is accepted and stripped before section checks. Anything else raises
    `KeysValidationError` — the API turns that into a 422 for the user.
    """
    if not text.strip():
        msg = "Файл ключей пуст."
        raise KeysValidationError(msg)
    try:
        parsed = yaml12.parse_yaml(text)
    except Exception as error:
        msg = "Файл ключей не является корректным YAML."
        raise KeysValidationError(msg) from error
    if parsed is None:
        msg = "Файл ключей пуст."
        raise KeysValidationError(msg)
    if not isinstance(parsed, dict):
        msg = "Файл ключей должен содержать словарь разделов."
        raise KeysValidationError(msg)

    working = dict(parsed)
    for key in _ENVELOPE_KEYS:
        working.pop(key, None)

    # YAML keeps only the last of repeated mapping keys, so a duplicated section
    # is invisible after parsing — detect it from the raw text first.
    seen_titles: set[str] = set()
    for raw_line in text.splitlines():
        match = _SECTION_RE.match(raw_line)
        if match is None:
            continue
        title = match.group(1).strip()
        if title in seen_titles and title not in _ENVELOPE_KEYS:
            msg = f"Раздел «{title}» встречается в файле больше одного раза."
            raise KeysValidationError(msg)
        seen_titles.add(title)

    document: KeysDocument = {}
    for title, values in working.items():
        if not isinstance(title, str) or title not in TITLE_LABELS:
            known = ", ".join(f"«{name}»" for name in TITLE_LABELS)
            msg = f"Неизвестный раздел ключей: «{title}». Ожидаются: {known}."
            raise KeysValidationError(msg)
        if not isinstance(values, list):
            msg = f"Раздел «{title}» должен быть списком значений."
            raise KeysValidationError(msg)
        if not all(isinstance(value, str) and value.strip() for value in values):
            msg = f"Раздел «{title}» должен содержать непустые строки."
            raise KeysValidationError(msg)
        if len({value for value in values}) != len(values):
            msg = f"Раздел «{title}» содержит дубликаты значений."
            raise KeysValidationError(msg)
        document[title] = [value for value in values if isinstance(value, str)]
    if not document:
        msg = "Файл ключей не содержит ни одного раздела."
        raise KeysValidationError(msg)
    return document
