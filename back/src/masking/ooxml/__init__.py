"""
Namespace-aware OOXML/OPC package transformer (Phase 5, brief §6).

Replaces the old python-docx-only traversal: every text-bearing part is found
through the OPC package inventory, parsed with a secure XML parser and rewritten
at the text-node level. Only modified ZIP entries are re-serialized; unchanged
entries are copied byte-for-byte.
"""

from .manifest import MANIFEST_NAME, build_mask_manifest, build_unmask_manifest
from .package import (
    CoverageReport,
    OoxmlPackageError,
    PartCategory,
    ProcessedPart,
    build_inventory,
    transform_package,
)

__all__ = [
    "MANIFEST_NAME",
    "CoverageReport",
    "OoxmlPackageError",
    "PartCategory",
    "ProcessedPart",
    "build_inventory",
    "build_mask_manifest",
    "build_unmask_manifest",
    "transform_package",
]
