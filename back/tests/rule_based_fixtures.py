"""Tiny helpers shared by the detector-corpus and span-merge suites.

Kept as a plain module (not a global conftest) so both suites import exactly
the same masker factory, label accessor and synthetic `Entity` builder.
"""

from src.masking.rule_based import Entity, RuleBasedMasker


def _masker() -> RuleBasedMasker:
    return RuleBasedMasker()


def _labels(text: str) -> list[str]:
    return [entity.label for entity in _masker().detect(text)]


def _entity(label: str, start: int, end: int, score: float = 1.0) -> Entity:
    return Entity(label=label, text="x" * (end - start), start=start, end=end, score=score)
