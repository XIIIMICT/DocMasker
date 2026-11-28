"""
OPC package inventory: URI-keyed part classification and coverage (§6.1).

No hardcoded namespace prefixes: parts are resolved through
`[Content_Types].xml` and relationship files via QName/URI. The inventory keeps
only metadata (DM-3) — no XML payload is decompressed or retained here. Secure
parsing is shared with the transform, which opens the same archive and reads
each part immediately before parsing it.
"""

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from defusedxml.lxml import fromstring as safe_fromstring

# lxml ships no PEP 561 type information; a dynamic import keeps static type
# checkers quiet while still using lxml's serializer/QName helpers at runtime.
etree = __import__("lxml.etree", fromlist=["etree"])

__all__ = [
    "CP",
    "CT",
    "DC",
    "DCMITYPE",
    "DCTERMS",
    "EP",
    "PR",
    "VT",
    "WP",
    "_CATEGORY_BY_PART",
    "_COPY_SAFE_CONTENT_TYPES",
    "_COPY_SAFE_PARTS",
    "_HYPERLINK_TAG",
    "_MEDIA_SUFFIXES",
    "_PARAGRAPH_TAG",
    "_PROCESSABLE_CATEGORIES",
    "_RELS_TAG",
    "_REL_TYPES",
    "_TEXT_ATTRS",
    "_TEXT_TAGS",
    "A",
    "CoverageReport",
    "OoxmlPackageError",
    "PartCategory",
    "ProcessedPart",
    "R",
    "W",
    "_Part",
    "_content_types",
    "_disposition",
    "_local",
    "_qname",
    "_read_part",
    "_relationships",
    "_resolve",
    "_secure_parse",
    "build_inventory",
]

# --- Namespaces by URI (prefix-independent) ---------------------------------
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
DCMITYPE = "http://purl.org/dc/dcmitype/"
EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

#: Text-bearing element QNames whose .text carries user-visible content.
_TEXT_TAGS: frozenset[str] = frozenset(
    {
        f"{{{W}}}t",
        f"{{{W}}}delText",
        f"{{{A}}}t",
        # Core properties (dc:creator, cp:lastModifiedBy, dc:title, …)
        f"{{{DC}}}creator",
        f"{{{DC}}}title",
        f"{{{DC}}}subject",
        f"{{{DC}}}description",
        f"{{{CP}}}lastModifiedBy",
        f"{{{CP}}}keywords",
        f"{{{CP}}}category",
        f"{{{DCTERMS}}}created",
        f"{{{DCTERMS}}}modified",
        # Extended properties: Company / Manager
        f"{{{EP}}}Company",
        f"{{{EP}}}Manager",
        # docProps/custom.xml user-defined property values.
        f"{{{VT}}}lpwstr",
        f"{{{VT}}}lpstr",
        f"{{{VT}}}bstr",
    }
)

#: Attribute allowlist for drawing alternative text (§6.2 item 10).
_TEXT_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {
        (f"{{{WP}}}docPr", "descr"),
        (f"{{{WP}}}docPr", "title"),
        (f"{{{WP}}}docPr", "name"),
        # Comment/tracked-change authors and initials can identify a person
        # even after the body text is masked (P0.3).
        (f"{{{W}}}comment", f"{{{W}}}author"),
        (f"{{{W}}}comment", f"{{{W}}}initials"),
        (f"{{{W}}}ins", f"{{{W}}}author"),
        (f"{{{W}}}del", f"{{{W}}}author"),
        (f"{{{W}}}moveFrom", f"{{{W}}}author"),
        (f"{{{W}}}moveTo", f"{{{W}}}author"),
        (f"{{{W}}}person", f"{{{W}}}author"),
    }
)

#: Logical paragraph containers: text nodes are grouped by the closest `w:p`.
_PARAGRAPH_TAG = f"{{{W}}}p"
#: Relationship parts: external Target may contain an email/URL (§6.2 item 3).
_RELS_TAG = f"{{{PR}}}Relationship"
_HYPERLINK_TAG = f"{{{W}}}hyperlink"

#: Text-bearing part categories keyed by relationship type URI / name.
_CATEGORY_BY_PART: dict[str, str] = {
    "word/document.xml": "main_document",
    "word/footnotes.xml": "footnotes",
    "word/endnotes.xml": "endnotes",
    "word/comments.xml": "comments",
    "docProps/core.xml": "core_properties",
    "docProps/app.xml": "app_properties",
    "docProps/custom.xml": "custom_properties",
}

_REL_TYPES: dict[str, str] = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header": "headers_footers",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer": "headers_footers",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes": "footnotes",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes": "endnotes",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments": "comments",
}


_PROCESSABLE_CATEGORIES = frozenset(
    {
        *_CATEGORY_BY_PART.values(),
        *_REL_TYPES.values(),
        "relationships",
        "generic_xml",
    }
)

#: Explicit processor/disposition registry (P0.3). Unknown XML is never
#: silently treated as safe: it is marked unprocessed and the package is
#: rejected instead of returning a misleading "complete" manifest.
_COPY_SAFE_PARTS = frozenset(
    {
        "word/styles.xml",
        "word/stylesWithEffects.xml",
        "word/numbering.xml",
        "word/settings.xml",
        "word/fontTable.xml",
        "word/webSettings.xml",
        "word/theme/theme1.xml",
        "word/commentsExtended.xml",
        "word/commentsIds.xml",
        "word/commentsExtensible.xml",
    }
)
_COPY_SAFE_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml",
        "application/vnd.openxmlformats-officedocument.theme+xml",
    }
)
_MEDIA_SUFFIXES = (".png", ".jpeg", ".jpg", ".gif", ".bmp", ".emf", ".wmf")


def _disposition(name: str, ctype: str, rel_category: str) -> str:
    """Classify one part into process / copy / reject semantics."""
    if name.endswith(".rels"):
        return "process"  # external relationship Target values are masked
    if name in _CATEGORY_BY_PART or rel_category in _REL_TYPES.values():
        return "process"
    if name == "[Content_Types].xml":
        return "copy"
    if name.endswith(_MEDIA_SUFFIXES):
        return "copy"
    if name.endswith(".bin") or "/embeddings/" in name:
        return "reject"
    if ctype in _COPY_SAFE_CONTENT_TYPES:
        return "copy"
    if name.endswith((".xml", ".rels")):
        # Chart/drawing text-bearing parts are not in the anonymisation scope:
        # mark them unprocessed and fail closed instead of claiming complete.
        if any(marker in ctype for marker in ("chart+xml", "spreadsheetml")):
            return "unprocessed"
        # Unknown XML (e.g. customXml/item1.xml) is inspected generically:
        # only text nodes are rewritten, never attributes or structure.
        return "process"
    return "copy"  # opaque binary with no embedded-object semantics


class OoxmlPackageError(ValueError):
    """A package is malformed, unsafe or contains unprocessable content."""


@dataclass(frozen=True, slots=True)
class PartCategory:
    name: str
    disposition: str  # "process" | "copy" | "reject"


@dataclass(frozen=True, slots=True)
class ProcessedPart:
    name: str
    category: str
    text_nodes: int


@dataclass(slots=True)
class CoverageReport:
    processed_parts: list[str] = field(default_factory=list)
    processed: list[ProcessedPart] = field(default_factory=list)
    unprocessed_text_parts: list[str] = field(default_factory=list)
    rejected_parts: list[str] = field(default_factory=list)

    def as_manifest(self) -> dict[str, Any]:
        return {
            "processed_parts": sorted(set(self.processed_parts)),
            "processed": [
                {"name": item.name, "category": item.category, "text_nodes": item.text_nodes}
                for item in sorted(self.processed, key=lambda item: item.name)
            ],
            "unprocessed_text_parts": sorted(set(self.unprocessed_text_parts)),
            "rejected_parts": sorted(set(self.rejected_parts)),
            "raster_image_ocr": "not_performed",
        }


@dataclass(frozen=True, slots=True)
class _Part:
    """OPC metadata only: no part payload is kept after inventory (DM-3)."""

    name: str
    content_type: str
    category: str
    disposition: str
    relationship_type: str
    relationship_mode: str
    is_xml: bool


def _qname(tag: str, uri: str) -> str:
    return f"{{{uri}}}{tag}"


def _local(tag: str) -> str:
    return etree.QName(tag).localname


def _secure_parse(data: bytes) -> etree._Element:
    """Parse one XML part with external entities disabled and bounded size."""
    if len(data) > 32 * 1024 * 1024:
        msg = "Слишком большая XML-часть документа."
        raise OoxmlPackageError(msg)
    try:
        return safe_fromstring(data, forbid_dtd=True, forbid_entities=True)
    except Exception as error:
        msg = "Документ содержит некорректную или небезопасную XML-часть."
        raise OoxmlPackageError(msg) from error


def _read_part(archive: zipfile.ZipFile, name: str) -> bytes:
    """Decompress one part exactly when it is about to be parsed (DM-3)."""
    return archive.read(name)


def _content_types(archive: zipfile.ZipFile) -> tuple[dict[str, str], dict[str, str]]:
    """Part name → content type, resolved by URI (no prefix assumptions)."""
    try:
        root = _secure_parse(archive.read("[Content_Types].xml"))
    except KeyError as error:
        msg = "Пакет DOCX не содержит [Content_Types].xml."
        raise OoxmlPackageError(msg) from error
    result: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for element in root:
        tag = _local(element.tag)
        if tag == "Override":
            name = element.get("PartName", "").lstrip("/")
            ctype = element.get("ContentType", "")
            if name:
                result[name] = ctype
        elif tag == "Default":
            extension = element.get("Extension", "").lower()
            ctype = element.get("ContentType", "")
            if extension:
                defaults[extension] = ctype
    return result, defaults


def _relationships(archive: zipfile.ZipFile, part_name: str) -> list[tuple[str, str, str]]:
    """(Type, Target, TargetMode) triples of one part's .rels, by QName."""
    if part_name == "":
        rels_path = "_rels/.rels"
    else:
        parent, _, leaf = part_name.rpartition("/")
        rels_path = f"{parent}/_rels/{leaf}.rels" if parent else f"_rels/{leaf}.rels"
    try:
        root = _secure_parse(archive.read(rels_path))
    except KeyError:
        return []
    result: list[tuple[str, str, str]] = []
    for element in root:
        if element.tag == _RELS_TAG:
            result.append(
                (
                    element.get("Type", ""),
                    element.get("Target", ""),
                    element.get("TargetMode", ""),
                )
            )
    return result


def _resolve(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base.rpartition("/")[0] + "/" + target).split("/")
    stack: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if stack:
                stack.pop()
        else:
            stack.append(part)
    return "/".join(stack)


def build_inventory(source: bytes | Path) -> tuple[list[_Part], CoverageReport]:
    """
    Deterministic OPC inventory, resolved by URI.

    Content type, relationship type, part name, category and disposition
    (§6.1). Shared parts are listed exactly once. The inventory retains only
    metadata — no XML payload is decompressed or held here (DM-3): the transform
    opens the same archive and reads each part immediately before parsing it.
    """
    coverage = CoverageReport()
    try:
        archive = zipfile.ZipFile(source if isinstance(source, Path) else io.BytesIO(source))
    except zipfile.BadZipFile as error:
        msg = "Файл не является корректным DOCX (повреждённый архив)."
        raise OoxmlPackageError(msg) from error
    with archive:
        names = {info.filename for info in archive.infolist() if not info.is_dir()}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            msg = "Файл не является корректным DOCX (отсутствуют части пакета Word)."
            raise OoxmlPackageError(msg)
        overrides, defaults = _content_types(archive)

        def content_type_of(name: str) -> str:
            if name in overrides:
                return overrides[name]
            return defaults.get(name.rsplit(".", 1)[-1].lower(), "")

        def safe_name(name: str) -> str:
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or "//" in name
                or ".." in name.split("/")
                or any(segment.startswith(".") and segment != ".rels" for segment in name.split("/"))
            ):
                msg = "Пакет DOCX содержит некорректный путь части."
                raise OoxmlPackageError(msg)
            return name

        by_name: dict[str, tuple[str, str]] = {}
        # Root relationships are resolved from the package root; every part's
        # relationships are resolved relative to that owning part (P0.3).
        rel_sources = ["", *[name for name in sorted(names) if not name.endswith(".rels")]]
        for owner in rel_sources:
            for rel_type, target, mode in _relationships(archive, owner):
                resolved = _resolve(owner, target)
                if resolved in names:
                    by_name[safe_name(resolved)] = (_REL_TYPES.get(rel_type, "other"), mode)
        for name in names:
            if name.startswith("word/") and name.endswith(".xml"):
                by_name.setdefault(name, ("other", ""))
            if name.startswith("docProps/") and name.endswith(".xml"):
                by_name.setdefault(name, ("other", ""))
            if name.endswith(".rels"):
                by_name.setdefault(name, ("relationships", "Internal"))
        by_name.setdefault("word/document.xml", (_CATEGORY_BY_PART["word/document.xml"], ""))

        parts: list[_Part] = []
        for name in sorted(names):
            safe_name(name)
            ctype = content_type_of(name)
            rel_category, rel_mode = by_name.get(name, ("other", ""))
            disposition = _disposition(name, ctype, rel_category)
            category = _CATEGORY_BY_PART.get(name, rel_category) if disposition == "process" else disposition
            if disposition == "process" and category in ("other", ""):
                category = "generic_xml"
            if disposition == "reject":
                coverage.rejected_parts.append(name)
            elif disposition == "unprocessed":
                coverage.unprocessed_text_parts.append(name)
            parts.append(
                _Part(
                    name=name,
                    content_type=ctype,
                    category=category,
                    disposition=disposition,
                    relationship_type=rel_category,
                    relationship_mode=rel_mode,
                    is_xml=name.endswith((".xml", ".rels")),
                )
            )
    return parts, coverage
