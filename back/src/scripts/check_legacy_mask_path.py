"""
Static guard: keep the deprecated quadratic `RuleBasedMasker.mask()` out of `back/src`.

Production text masking is `service.mask_text` (one validated join,
O(|text| + entities)). `mask()` rewrites the text with one full-string slice
per entity (O(|text| * entities)) and is retained only as a test/benchmark
reference for `detect()`. A regression that wires it into request handling
must fail CI before it can merge.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

LEGACY_METHOD = "mask"
_DYNAMIC_LOOKUPS = frozenset({"getattr", "methodcaller"})


@dataclass(frozen=True)
class Violation:
    """One forbidden reference to the legacy method."""

    path: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: {self.reason}"


def _is_legacy_reference(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == LEGACY_METHOD
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DYNAMIC_LOOKUPS:
        return any(isinstance(arg, ast.Constant) and arg.value == LEGACY_METHOD for arg in node.args[1:2])
    return False


def find_violations(root: Path, *, allowed: Iterable[Path] = ()) -> list[Violation]:
    """
    Return every `mask()` attribute or dynamic lookup under `root`.

    `allowed` holds files deliberately exempt from the rule; it is empty in
    production because tests and benchmarks live outside `back/src`.
    """
    allowed_paths = {path.resolve() for path in allowed}
    violations: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        if path.resolve() in allowed_paths:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Attribute, ast.Call)):
                continue
            if _is_legacy_reference(node):
                violations.append(Violation(path, node.lineno, f"legacy {LEGACY_METHOD}() reference"))
    return violations


def default_root() -> Path:
    """Return the canonical `back/src` tree the CI guard scans."""
    return Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: exit 1 with an itemised list when a violation exists."""
    root = Path(argv[0]).resolve() if argv else default_root()
    violations = find_violations(root)
    if violations:
        sys.stderr.write("Deprecated RuleBasedMasker.mask() must not be used under back/src:\n")
        for violation in violations:
            sys.stderr.write(f"  {violation}\n")
        sys.stderr.write("Use src.masking.service.mask_text() (one validated join) instead.\n")
        return 1
    sys.stdout.write(f"No legacy mask() references under {root.as_posix()}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
