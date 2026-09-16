"""
Demasking keys: a numbered placeholder per masked entity, and its YAML form.

Masking is reversible: every distinct entity occurrence gets its own placeholder
``[LABEL_N]`` (the counter is per label, in document order), and the original
value is recorded in a keys file. Restoring the document is then a plain
placeholder→value substitution, so declension forms are preserved exactly —
which is why two spellings of the same organisation are **two** entries, not
one.

The keys file is YAML keyed by the Russian section titles (`LABEL_TITLES`), e.g.

```yaml
Физлица:
  - Иван Петров
Номера телефонов:
  - +7 900 123-45-67
```

Sections with no entities are omitted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import yaml12

from .constants import LABEL_TITLES, mask_marker

#: Value of a single keys section (a list of original strings).
KeysSection = list[str]
#: The whole keys document: Russian title -> ordered values.
KeysDocument = dict[str, KeysSection]


class MaskKeys:
    """
    Placeholders for masked entities, numbered per label in detection order.

    Entities are keyed by their exact text: the **same** string always reuses
    the same placeholder and yields a single key entry, so a phone repeated in a
    document is masked as one ``[PHONE_1]`` and restored from one key. Distinct
    wordings stay separate — `АО «Росхим»` and `Акционерного общества «Росхим»`
    are two keys, which is what preserves the original case forms on restoring.
    """

    def __init__(self) -> None:
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
        Return the keys as a title→values mapping, in `LABEL_TITLES` order.

        Empty sections are omitted; the values are ordered by their number.
        """
        by_label: dict[str, list[tuple[int, str]]] = {}
        for (label, text), number in self._numbers.items():
            by_label.setdefault(label, []).append((number, text))

        document: KeysDocument = {}
        for label, title in LABEL_TITLES.items():
            entries = sorted(by_label.get(label, []))
            if entries:
                document[title] = [value for _number, value in entries]
        return document

    def to_yaml(self) -> str:
        """Render the keys as a YAML document (empty string when there are none)."""
        return format_keys_yaml(self.document())


def format_keys_yaml(document: Mapping[str, Iterable[str]]) -> str:
    """Serialize a keys document to YAML, or return `""` when it is empty."""
    sections = {title: list(values) for title, values in document.items() if values}
    if not sections:
        return ""
    return yaml12.format_yaml(sections)


def parse_keys_yaml(text: str) -> KeysDocument:
    """Parse a keys document; raises `TypeError`/`ValueError` on malformed input."""
    parsed = yaml12.parse_yaml(text)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        msg = "Файл ключей должен содержать словарь списков."
        raise TypeError(msg)
    document: KeysDocument = {}
    for title, values in parsed.items():
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            msg = "Каждый раздел ключей должен быть списком строк."
            raise TypeError(msg)
        document[str(title)] = list(values)
    return document
