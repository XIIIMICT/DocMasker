"""
One immutable masking configuration (Phase 4, brief §5).

At startup this module loads `labels.yaml`, the lexicon files and the private
`exceptions.yaml` from `MASKING_CONFIG_DIR`, validates them strictly and freezes
one snapshot. No hot reload: a running job uses the same snapshot, and its
digest is part of the versioned cache key. Algorithms (regex/parsers/span
merging) stay in Python — configuration contains literal data only.
"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml12

from src.config import get_settings

SCHEMA_VERSION = 1
MATCH_MODES = frozenset({"exact", "case_insensitive", "token_sequence"})
KNOWN_LABELS = frozenset({"person", "phone", "email", "link", "organization", "address"})


class MaskingConfigError(ValueError):
    """Invalid masking configuration — startup must fail clearly."""


def _normalize(value: str) -> str:
    """NFC, trim, collapse whitespace. `ё`/`е` are NOT equivalent (tested)."""
    return " ".join(unicodedata.normalize("NFC", value).split())


@dataclass(frozen=True, slots=True)
class ExceptionValue:
    """One literal exception entry with a conservative match mode."""

    value: str
    match: str
    variants: tuple[str, ...] = ()

    @property
    def normalized(self) -> str:
        return _normalize(self.value).casefold()


@dataclass(frozen=True, slots=True)
class LabelCatalogue:
    """Canonical label ids → Russian keys-section titles; sequence = order."""

    entries: tuple[tuple[str, str], ...]

    def titles(self) -> dict[str, str]:
        return dict(self.entries)

    def ids(self) -> tuple[str, ...]:
        return tuple(label for label, _title in self.entries)

    def title_to_label(self) -> dict[str, str]:
        return {title: label for label, title in self.entries}


@dataclass(frozen=True, slots=True)
class MaskingConfig:
    """Immutable validated snapshot of every masking data file."""

    labels: LabelCatalogue
    always_mask: dict[str, tuple[ExceptionValue, ...]]
    never_mask: dict[str, tuple[ExceptionValue, ...]]
    org_abbreviations: tuple[str, ...]
    org_legal_phrases: tuple[tuple[str, ...], ...]
    org_lead_words: tuple[str, ...]
    person_roles: frozenset[str]
    address_abbreviations: frozenset[str]
    address_keywords: tuple[str, ...]
    url_trim_chars: str
    org_separators: str
    address_trim_chars: str
    digest: str

    def titles(self) -> dict[str, str]:
        return self.labels.titles()

    def title_to_label(self) -> dict[str, str]:
        return self.labels.title_to_label()

    def label_ids(self) -> tuple[str, ...]:
        return self.labels.ids()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        parsed = yaml12.parse_yaml(path.read_text(encoding="utf-8"))
    except Exception as error:
        msg = f"Конфигурация маскирования: не удалось прочитать {path.name}."
        raise MaskingConfigError(msg) from error
    if not isinstance(parsed, dict) or parsed.get("schema_version") != SCHEMA_VERSION:
        msg = f"Конфигурация маскирования: неверная схема в {path.name}."
        raise MaskingConfigError(msg)
    return cast("dict[str, Any]", parsed)


def _string_list(container: dict[str, Any], key: str, path: Path) -> tuple[str, ...]:
    values = container.get(key, [])
    if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
        msg = f"Конфигурация маскирования: {key} в {path.name} должен быть списком непустых строк."
        raise MaskingConfigError(msg)
    return tuple(_normalize(item) for item in values)


def _exceptions(container: dict[str, Any], side: str, path: Path) -> dict[str, tuple[ExceptionValue, ...]]:
    raw = container.get(side, {})
    if not isinstance(raw, dict):
        msg = f"Конфигурация маскирования: {side} в {path.name} должен быть словарём."
        raise MaskingConfigError(msg)
    result: dict[str, tuple[ExceptionValue, ...]] = {}
    seen: set[str] = set()
    for label, entries in raw.items():
        if label not in KNOWN_LABELS:
            msg = f"Конфигурация маскирования: неизвестный тип «{label}» в {path.name}."
            raise MaskingConfigError(msg)
        if not isinstance(entries, list):
            msg = f"Конфигурация маскирования: {side}.{label} в {path.name} должен быть списком."
            raise MaskingConfigError(msg)
        parsed: list[ExceptionValue] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
                msg = f"Конфигурация маскирования: некорректная запись в {path.name}."
                raise MaskingConfigError(msg)
            mode = entry.get("match", "case_insensitive")
            if mode not in MATCH_MODES:
                msg = f"Конфигурация маскирования: неизвестный режим совпадения «{mode}» в {path.name}."
                raise MaskingConfigError(msg)
            value = _normalize(entry["value"])
            if not value:
                msg = f"Конфигурация маскирования: пустое значение в {path.name}."
                raise MaskingConfigError(msg)
            variants = entry.get("variants", [])
            if not isinstance(variants, list) or not all(isinstance(item, str) for item in variants):
                msg = f"Конфигурация маскирования: некорректные варианты в {path.name}."
                raise MaskingConfigError(msg)
            normalized = value.casefold()
            if normalized in seen:
                msg = f"Конфигурация маскирования: дублирующееся значение в {path.name}."
                raise MaskingConfigError(msg)
            seen.add(normalized)
            parsed.append(ExceptionValue(value, mode, tuple(_normalize(item) for item in variants)))
        result[label] = tuple(parsed)
    return result


def load_config(root: str | Path | None = None) -> MaskingConfig:
    """Load, validate and freeze one immutable configuration snapshot."""
    base = Path(root) if root is not None else Path(get_settings().masking_config_dir)
    if not base.is_dir():
        # Host/dev fallback: the checked-in defaults next to the package.
        base = Path(__file__).resolve().parents[2] / "config" / "masking"

    labels_path = base / "labels.yaml"
    labels_doc = _load_yaml(labels_path)
    raw_labels = labels_doc.get("labels")
    if not isinstance(raw_labels, list) or not raw_labels:
        msg = "Конфигурация маскирования: labels.yaml должен содержать непустой список labels."
        raise MaskingConfigError(msg)
    entries: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for item in raw_labels:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("title"), str):
            msg = "Конфигурация маскирования: некорректная запись labels.yaml."
            raise MaskingConfigError(msg)
        label, title = _normalize(item["id"]), _normalize(item["title"])
        if label not in KNOWN_LABELS or label in seen_ids or title in seen_titles:
            msg = "Конфигурация маскирования: дублирующийся или неизвестный label в labels.yaml."
            raise MaskingConfigError(msg)
        seen_ids.add(label)
        seen_titles.add(title)
        entries.append((label, title))
    if "order" in labels_doc:
        msg = "Конфигурация маскирования: поле order больше не поддерживается."
        raise MaskingConfigError(msg)

    lex = base / "lexicons"
    org_doc = _load_yaml(lex / "organizations.yaml")
    phrases = org_doc.get("legal_form_phrases", [])
    if not isinstance(phrases, list) or not all(
        isinstance(form, list) and form and all(isinstance(word, str) for word in form) for form in phrases
    ):
        msg = "Конфигурация маскирования: некорректные legal_form_phrases."
        raise MaskingConfigError(msg)

    person_doc = _load_yaml(lex / "persons.yaml")
    address_doc = _load_yaml(lex / "addresses.yaml")
    common_doc = _load_yaml(lex / "common.yaml")

    exceptions_path = base / "exceptions.yaml"
    if not exceptions_path.is_file():
        exceptions_path = base / "exceptions.example.yaml"
    exceptions_doc = _load_yaml(exceptions_path)
    always = _exceptions(exceptions_doc, "always_mask", exceptions_path)
    never = _exceptions(exceptions_doc, "never_mask", exceptions_path)
    conflicts = {value.normalized for group in always.values() for value in group} & {
        value.normalized for group in never.values() for value in group
    }
    if conflicts:
        msg = "Конфигурация маскирования: конфликт always_mask и never_mask."
        raise MaskingConfigError(msg)

    # Digest over the canonicalized, validated data (never over raw values in logs).
    digest_payload = {
        "labels": entries,
        "always": {k: [v.normalized for v in values] for k, values in always.items()},
        "never": {k: [v.normalized for v in values] for k, values in never.items()},
        "org_abbrev": _string_list(org_doc, "legal_form_abbreviations", labels_path),
        "org_phrases": phrases,
        "org_lead": _string_list(org_doc, "lead_words", labels_path),
        "person_roles": _string_list(person_doc, "role_exclusions", labels_path),
        "address_abbrev": _string_list(address_doc, "abbreviations", labels_path),
        "address_keywords": _string_list(address_doc, "keywords", labels_path),
    }
    digest = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    return MaskingConfig(
        labels=LabelCatalogue(tuple(entries)),
        always_mask=always,
        never_mask=never,
        org_abbreviations=_string_list(org_doc, "legal_form_abbreviations", labels_path),
        org_legal_phrases=tuple(tuple(form) for form in phrases),
        org_lead_words=_string_list(org_doc, "lead_words", labels_path),
        person_roles=frozenset(_string_list(person_doc, "role_exclusions", labels_path)),
        address_abbreviations=frozenset(_string_list(address_doc, "abbreviations", labels_path)),
        address_keywords=_string_list(address_doc, "keywords", labels_path),
        url_trim_chars=str(common_doc.get("url_trim_chars", "")),
        org_separators=str(common_doc.get("organization_separators", "")),
        address_trim_chars=str(common_doc.get("address_trim_chars", "")),
        digest=digest,
    )


@lru_cache(maxsize=1)
def get_config() -> MaskingConfig:
    """Process-wide immutable snapshot (loaded once at first use / startup)."""
    return load_config()


def config_digest() -> str:
    """Non-reversible snapshot digest for cache keys and safe logs."""
    return get_config().digest
