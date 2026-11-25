"""Tests for the single masking configuration and exception files (§5.6/§5.7)."""

import shutil
from pathlib import Path

import pytest

from src.masking.config import MaskingConfigError, load_config
from src.masking.rule_based import RuleBasedMasker

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "config" / "masking"


def _copy_config(tmp_path: Path) -> Path:
    root = tmp_path / "config"
    shutil.copytree(DEFAULT_ROOT, root)
    # exceptions.yaml is private/gitignored and absent in CI checkouts; the
    # loader falls back to the committed example. The digest test edits that
    # path, so materialise it explicitly for deterministic runs everywhere.
    exceptions = root / "exceptions.yaml"
    if not exceptions.exists():
        shutil.copyfile(root / "exceptions.example.yaml", exceptions)
    return root


def test_default_config_is_valid_and_immutable() -> None:
    config = load_config(DEFAULT_ROOT)
    assert config.label_ids() == (
        "person",
        "phone",
        "email",
        "link",
        "organization",
        "address",
    )
    assert config.titles()["person"] == "Физлица"
    assert len(config.digest) == 64
    assert isinstance(config.always_mask["organization"], tuple)


def test_exception_changes_alter_the_digest(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    first = load_config(root).digest
    path = root / "exceptions.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"Росхим"', '"Росхим-Тест"'),
        encoding="utf-8",
    )
    assert load_config(root).digest != first


def test_malformed_config_fails(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    (root / "labels.yaml").write_text("schema_version: 2\nlabels: []\n", encoding="utf-8")
    with pytest.raises(MaskingConfigError):
        load_config(root)


def test_unknown_label_in_exceptions_fails(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    (root / "exceptions.yaml").write_text(
        "schema_version: 1\nalways_mask:\n  banana: []\nnever_mask: {}\n", encoding="utf-8"
    )
    with pytest.raises(MaskingConfigError, match="неизвестный тип"):
        load_config(root)


def test_unknown_match_mode_fails(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    (root / "exceptions.yaml").write_text(
        "schema_version: 1\nalways_mask:\n  organization:\n    - value: X\n      match: regex\nnever_mask: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(MaskingConfigError, match="режим"):
        load_config(root)


def test_conflicting_always_and_never_mask_fails(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    (root / "exceptions.yaml").write_text(
        "schema_version: 1\nalways_mask:\n  organization:\n    - value: X\n"
        "      match: exact\nnever_mask:\n  organization:\n    - value: X\n"
        "      match: exact\n",
        encoding="utf-8",
    )
    with pytest.raises(MaskingConfigError, match="конфликт"):
        load_config(root)


def test_duplicate_normalized_value_fails(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    (root / "exceptions.yaml").write_text(
        "schema_version: 1\nalways_mask:\n  organization:\n    - value: X\n"
        "      match: exact\n    - value: x\n      match: exact\nnever_mask: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(MaskingConfigError, match="дублирующееся"):
        load_config(root)


def test_order_field_is_rejected(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    path = root / "labels.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "order: 1\n", encoding="utf-8")
    with pytest.raises(MaskingConfigError, match="order"):
        load_config(root)


# --- §5.7 exception behavior (using the checked-in default config) -----------


def test_roskhim_force_mask_still_works() -> None:
    masker = RuleBasedMasker()
    assert masker.mask("ООО Росхим") == "[ORGANIZATION]"
    assert masker.mask("росхим") == "[ORGANIZATION]"
    assert "Росхим" not in masker.mask("Росхим")
    # Explicit variants.
    assert masker.mask("Росхимом") == "[ORGANIZATION]"


def test_never_mask_person_exception() -> None:
    masker = RuleBasedMasker()
    assert masker.mask("Иван Иванов") == "Иван Иванов"
    # Adjacent unrelated PII must still be masked (no substring suppression).
    assert masker.mask("Иван Иванов, ivan@mail.ru") == "Иван Иванов, [EMAIL]"


def test_never_mask_does_not_suppress_a_larger_span() -> None:
    masker = RuleBasedMasker()
    # The configured phrase is only part of a longer FIO: suppression must not
    # drop the whole detected span (it must still be masked as a person).
    assert masker.mask("Иван Иванов Сергеевич") == "[PERSON]"


def test_forced_span_gets_a_normal_placeholder_and_restores() -> None:
    from src.masking.mapping import MaskKeys

    masker = RuleBasedMasker()
    text = "Росхим"
    entities = masker.detect(text)
    assert len(entities) == 1
    assert entities[0].label == "organization"
    keys = MaskKeys()
    marker = keys.placeholder(entities[0].label, entities[0].text)
    assert marker == "[ORGANIZATION_1]"
    assert keys.to_yaml().count("Росхим") == 1


def test_validate_config_cli_succeeds_and_rejects_bad_config(tmp_path: Path) -> None:
    from src.masking.validate_config import main

    assert main(["--dir", str(DEFAULT_ROOT)]) == 0
    root = _copy_config(tmp_path)
    (root / "labels.yaml").write_text("labels: []\n", encoding="utf-8")
    assert main(["--dir", str(root)]) == 1
