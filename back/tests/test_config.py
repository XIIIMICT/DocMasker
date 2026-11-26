"""Tests for the single masking configuration and exception files (§5.6/§5.7)."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from src.config import Settings
from src.jobs.cache import build_cache_key, fingerprint_for
from src.masking import rule_based
from src.masking.config import MaskingConfig, MaskingConfigError, load_config
from src.masking.rule_based import Entity, RuleBasedMasker, _exception_pattern, _ExceptionPattern, _suppressed

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


# --- P1.2: digest covers every effective value; modes have real semantics -----


def _rewrite(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, (relative, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _append_to_quoted(root: Path, relative: str, key: str, suffix: str) -> None:
    path = root / relative
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            head, value = line.split(":", 1)
            stripped = value.strip()
            assert stripped.startswith('"'), line
            assert stripped.endswith('"'), line
            lines[index] = f'{head}: {stripped[:-1]}{suffix}"'
            break
    else:
        raise AssertionError(key)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _change_label_order(root: Path) -> None:
    (root / "labels.yaml").write_text(
        "schema_version: 1\nlabels:\n"
        "  - id: address\n    title: Адреса\n"
        "  - id: organization\n    title: Организации\n"
        "  - id: link\n    title: Ссылки\n"
        "  - id: email\n    title: Email\n"
        "  - id: phone\n    title: Номера телефонов\n"
        "  - id: person\n    title: Физлица\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda root: _rewrite(root, "labels.yaml", "title: Email", "title: E-mail"),
        _change_label_order,
        lambda root: _rewrite(root, "lexicons/organizations.yaml", "  - ООО\n", "  - ООО\n  - TESTORG\n"),
        lambda root: _rewrite(root, "lexicons/organizations.yaml", "[акционерный, общество]", "[акционерный, тест]"),
        lambda root: _rewrite(root, "lexicons/organizations.yaml", "  - организация", "  - TESTLEAD"),
        lambda root: _rewrite(root, "lexicons/persons.yaml", "  - секретарь", "  - TESTROLE"),
        lambda root: _rewrite(root, "lexicons/addresses.yaml", "  - г\n", "  - тест\n"),
        lambda root: _rewrite(root, "lexicons/addresses.yaml", '  - "г."', '  - "тест."'),
        lambda root: _append_to_quoted(root, "lexicons/common.yaml", "url_trim_chars", "+"),
        lambda root: _append_to_quoted(root, "lexicons/common.yaml", "organization_separators", "+"),
        lambda root: _append_to_quoted(root, "lexicons/common.yaml", "address_trim_chars", "+"),
        lambda root: _rewrite(root, "exceptions.yaml", "match: case_insensitive", "match: exact"),
        lambda root: _rewrite(root, "exceptions.yaml", '"Росхима"', '"Росхим-Тест-Вариант"'),
        lambda root: _rewrite(
            root,
            "exceptions.yaml",
            'variants: ["Росхима", "Росхиме", "Росхимом"]',
            'variants: ["Росхима", "Росхиме", "Росхимом", "Росхим-Доп"]',
        ),
        lambda root: _rewrite(root, "exceptions.yaml", '"Иван Иванов"', '"Иван Петров"'),
    ],
)
def test_every_effective_dimension_changes_digest(tmp_path: Path, change: Callable[[Path], None]) -> None:
    root = _copy_config(tmp_path)
    first = load_config(root).digest
    change(root)
    assert load_config(root).digest != first


def test_formatting_and_comments_do_not_change_digest(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    first = load_config(root).digest
    path = root / "exceptions.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# комментарий\n# ещё один\n", encoding="utf-8")
    assert load_config(root).digest == first


def _exceptions_config(
    root: Path,
    side: str,
    value: str,
    mode: str,
    variants: tuple[str, ...] = (),
) -> MaskingConfig:
    text = f'schema_version: 1\n{side}:\n  organization:\n    - value: "{value}"\n      match: {mode}\n'
    if variants:
        text += f"      variants: [{', '.join(f'"{item}"' for item in variants)}]\n"
    other = "never_mask" if side == "always_mask" else "always_mask"
    text += f"{other}: {{}}\n"
    (root / "exceptions.yaml").write_text(text, encoding="utf-8")
    return load_config(root)


def _masker_with_config(monkeypatch: pytest.MonkeyPatch, config: MaskingConfig) -> RuleBasedMasker:
    monkeypatch.setattr(rule_based, "_cfg", lambda: config)
    return RuleBasedMasker(labels=config.label_ids())


def _matches(patterns: _ExceptionPattern, text: str) -> list[str]:
    return [match.group(0) for match in patterns.finditer(text)]


def test_declared_match_modes_have_distinct_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _copy_config(tmp_path)

    exact = _exceptions_config(root, "always_mask", "Росхим-Тест", "exact")
    exact_patterns = _exception_pattern(exact.always_mask["organization"])
    assert _matches(exact_patterns, "Росхим-Тест") == ["Росхим-Тест"]
    assert _matches(exact_patterns, "росхим-тест") == []
    assert "[ORGANIZATION]" in _masker_with_config(monkeypatch, exact).mask("Росхим-Тест")

    insensitive = _exceptions_config(root, "always_mask", "Росхим-Тест", "case_insensitive")
    insensitive_patterns = _exception_pattern(insensitive.always_mask["organization"])
    assert _matches(insensitive_patterns, "росхим-тест") == ["росхим-тест"]

    token = _exceptions_config(root, "always_mask", "Росхим Тест", "token_sequence")
    token_patterns = _exception_pattern(token.always_mask["organization"])
    assert _matches(token_patterns, "Росхим   Тест")
    assert _matches(token_patterns, "росхим тест")
    assert not _matches(token_patterns, "Тест Росхим")
    assert not _matches(token_patterns, "Росхим")

    never = _exceptions_config(root, "never_mask", "Росхим-Тест", "exact")
    never_patterns = _exception_pattern(never.never_mask["organization"])
    assert _suppressed(Entity("organization", "Росхим-Тест", 0, 11), {"organization": never_patterns})
    assert not _suppressed(Entity("organization", "росхим-тест", 0, 11), {"organization": never_patterns})


def test_match_mode_change_alone_changes_digest(tmp_path: Path) -> None:
    root = _copy_config(tmp_path)
    exact = _exceptions_config(root, "always_mask", "Росхим-Тест", "exact")
    insensitive = _exceptions_config(root, "always_mask", "Росхим-Тест", "case_insensitive")
    assert exact.digest != insensitive.digest


@pytest.mark.parametrize(
    ("variants", "message"),
    [
        (("",), "пустой вариант"),
        (("Один", "один"), "дублирующийся вариант"),
        (("базовое",), "дублирующееся значение"),
    ],
)
def test_invalid_variants_fail(tmp_path: Path, variants: tuple[str, ...], message: str) -> None:
    root = _copy_config(tmp_path)
    with pytest.raises(MaskingConfigError, match=message):
        _exceptions_config(root, "always_mask", "Базовое", "exact", variants)


def test_snapshot_digest_always_participates_in_cache_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.masking.config as config_module

    settings = Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://dm:dm@localhost:5432/dm",
            "cache_key_secret": "unit-test-stable-secret-32chars",
        }
    )
    monkeypatch.setattr(config_module, "config_digest", lambda: "a" * 64)
    first = build_cache_key(settings, fingerprint_for("mask_text", input_bytes=b"x"))
    monkeypatch.setattr(config_module, "config_digest", lambda: "b" * 64)
    second = build_cache_key(settings, fingerprint_for("mask_text", input_bytes=b"x"))
    assert first != second
