"""Proof that the AST guard accepts the linear path and rejects the quadratic one."""

from collections.abc import Callable
from pathlib import Path

import pytest

from src.masking.rule_based import Entity, RuleBasedMasker
from src.scripts.check_legacy_mask_path import default_root, find_violations, main

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


class _StubMasker(RuleBasedMasker):
    """Avoid building a MorphVocab just to test the deprecation warning."""

    def __init__(self) -> None:
        pass

    def detect(
        self,
        text: str,
        *,
        checkpoint: Callable[[], None] | None = None,
        prefilter: bool = False,
    ) -> list[Entity]:
        return []


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_real_src_tree_has_no_legacy_reference() -> None:
    assert find_violations(_SRC_ROOT) == []


def test_cli_defaults_to_the_real_src_tree() -> None:
    assert default_root() == _SRC_ROOT
    assert main([]) == 0


def test_direct_call_is_a_violation(tmp_path: Path) -> None:
    path = _write(tmp_path, "router.py", "def handle(masker, text):\n    return masker.mask(text)\n")
    violations = find_violations(tmp_path)
    assert [(violation.path, violation.line) for violation in violations] == [(path, 2)]


def test_dynamic_lookup_is_a_violation(tmp_path: Path) -> None:
    _write(tmp_path, "worker.py", "def handle(masker, text):\n    return getattr(masker, 'mask')(text)\n")
    assert len(find_violations(tmp_path)) == 1


def test_linear_path_is_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "service.py",
        "from src.masking.service import mask_text\n"
        "def handle(masker, text, keys):\n"
        "    entities = masker.detect(text)\n"
        "    return mask_text(text, entities, keys)\n",
    )
    assert find_violations(tmp_path) == []


def test_allowlisted_file_is_skipped(tmp_path: Path) -> None:
    path = _write(tmp_path, "reference.py", "value = masker.mask(text)\n")
    assert find_violations(tmp_path, allowed=[path]) == []
    assert len(find_violations(tmp_path)) == 1


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "router.py", "masker.mask(text)\n")
    assert main([str(tmp_path)]) == 1
    assert "router.py:1" in capsys.readouterr().err
    _write(tmp_path, "router.py", "mask_text(text, entities, keys)\n")
    assert main([str(tmp_path)]) == 0
    assert "No legacy" in capsys.readouterr().out


def test_deprecated_mask_warns() -> None:
    with pytest.warns(DeprecationWarning, match=r"RuleBasedMasker\.mask\(\) is deprecated"):
        masked = _StubMasker().mask("abc")
    assert masked == "abc"
