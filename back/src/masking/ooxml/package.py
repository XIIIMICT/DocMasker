"""
OPC package inventory and namespace-aware text-node transformer (§6.1–6.4).

No hardcoded namespace prefixes: parts are resolved through
`[Content_Types].xml` and relationship files via QName/URI. XML is parsed with
`defusedxml.lxml` (no external entities, no network). Text nodes are collected
per logical container (`w:p` or a whole metadata/relationship part) in document
order; a detected entity spanning several nodes becomes ONE placeholder. Only
modified ZIP entries are re-serialized — all other entries are copied verbatim.
"""

import io
import zipfile
from dataclasses import dataclass, field
from typing import Any

from defusedxml.lxml import fromstring as safe_fromstring

# lxml ships no PEP 561 type information; a dynamic import keeps static type
# checkers quiet while still using lxml's serializer/QName helpers at runtime.
etree = __import__("lxml.etree", fromlist=["etree"])

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
    }
)

#: Attribute allowlist for drawing alternative text (§6.2 item 10).
_TEXT_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {
        (f"{{{WP}}}docPr", "descr"),
        (f"{{{WP}}}docPr", "title"),
        (f"{{{WP}}}docPr", "name"),
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
}

_REL_TYPES: dict[str, str] = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header": "headers_footers",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer": "headers_footers",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes": "footnotes",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes": "endnotes",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments": "comments",
}


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
            "unprocessed_text_parts": sorted(set(self.unprocessed_text_parts)),
            "rejected_parts": sorted(set(self.rejected_parts)),
            "raster_image_ocr": "not_performed",
        }


@dataclass(frozen=True, slots=True)
class _Part:
    name: str
    data: bytes
    category: str
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


def _content_types(archive: zipfile.ZipFile) -> dict[str, str]:
    """Part name → content type, resolved by URI (no prefix assumptions)."""
    try:
        root = _secure_parse(archive.read("[Content_Types].xml"))
    except KeyError as error:
        msg = "Пакет DOCX не содержит [Content_Types].xml."
        raise OoxmlPackageError(msg) from error
    result: dict[str, str] = {}
    for element in root:
        if _local(element.tag) == "Override":
            name = element.get("PartName", "").lstrip("/")
            ctype = element.get("ContentType", "")
            if name:
                result[name] = ctype
    return result


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


def build_inventory(data: bytes) -> tuple[list[_Part], CoverageReport]:
    """
    Deterministic OPC inventory, resolved by URI.

    Content type, relationship type, part name, processor and disposition
    (§6.1). Shared parts are listed exactly once.
    """
    coverage = CoverageReport()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        msg = "Файл не является корректным DOCX (повреждённый архив)."
        raise OoxmlPackageError(msg) from error
    with archive:
        names = {info.filename for info in archive.infolist() if not info.is_dir()}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            msg = "Файл не является корректным DOCX (отсутствуют части пакета Word)."
            raise OoxmlPackageError(msg)
        _content_types(archive)

        # Root + main-document relationships decide which parts are text-bearing,
        # by Type URI (not filename guesses). Shared parts appear once.
        by_name: dict[str, tuple[str, str]] = {}
        for owner in ("", "word/document.xml"):
            for rel_type, target, mode in _relationships(archive, owner):
                resolved = _resolve("word/document.xml", target)
                if resolved in names:
                    by_name[resolved] = (_REL_TYPES.get(rel_type, "other"), mode)
        for name in names:
            if name.startswith("word/") and name.endswith((".xml", ".rels")):
                by_name.setdefault(name, ("other", ""))
        for name in names:
            if name.startswith("docProps/") and name.endswith(".xml"):
                by_name.setdefault(name, ("other", ""))
        # Relationships files themselves carry external targets (and their own
        # parts' rels may contain hyperlinks).
        for name in names:
            if name.endswith(".rels"):
                by_name.setdefault(name, ("relationships", "Internal"))
        by_name.setdefault("word/document.xml", (_CATEGORY_BY_PART["word/document.xml"], ""))

        parts: list[_Part] = []
        for name in sorted(names):
            if name == "[Content_Types].xml":
                parts.append(_Part(name, archive.read(name), "content_types", is_xml=True))
                continue
            rel_category, _mode = by_name.get(name, ("other", ""))
            category = _CATEGORY_BY_PART.get(name, rel_category)
            is_xml = name.endswith((".xml", ".rels"))
            if name.endswith((".png", ".jpeg", ".jpg", ".gif", ".bmp", ".emf", ".wmf")):
                # Raster/vector pixels are untouched — documented limitation
                # (§6.4), not an unprocessed *text* part and never a blocker.
                parts.append(_Part(name, archive.read(name), "media", is_xml=False))
                continue
            if name.endswith(".bin") or "/embeddings/" in name:
                # Embedded OLE/binary packages cannot be inspected safely.
                coverage.rejected_parts.append("embedded_objects")
                parts.append(_Part(name, archive.read(name), "embedded_object", is_xml=False))
                continue
            if is_xml:
                parts.append(_Part(name, archive.read(name), category, is_xml=True))
            else:
                parts.append(_Part(name, archive.read(name), "other_binary", is_xml=False))
    return parts, coverage


@dataclass(slots=True)
class _TextRef:
    """One editable text slice: an element's .text or an allowed attribute."""

    element: etree._Element
    is_attribute: bool = False
    attr_name: str = ""

    def get(self) -> str:
        if self.is_attribute:
            return self.element.get(self.attr_name) or ""
        return self.element.text or ""

    def set(self, value: str) -> None:
        if self.is_attribute:
            self.element.set(self.attr_name, value)
        else:
            self.element.text = value


@dataclass(slots=True)
class _Container:
    """A logical paragraph (or whole small part) with its ordered text refs."""

    refs: list[_TextRef]
    part_name: str
    category: str

    def text(self) -> str:
        return "".join(ref.get() for ref in self.refs)


def _containers(root: etree._Element, part_name: str, category: str) -> list[_Container]:
    """
    Collect editable text refs grouped by logical container.

    Namespace URIs (not prefixes) drive tag matching, so a document using a
    default namespace or custom prefixes is processed identically. Shared parts
    are listed once by `build_inventory`, so no double processing occurs.
    """
    result: list[_Container] = []
    by_para: dict[int, list[_TextRef]] = {}
    standalone: list[_TextRef] = []

    for element in root.iter():
        tag = element.tag if isinstance(element.tag, str) else ""
        if tag in _TEXT_TAGS and element.text:
            para = _closest_paragraph(element)
            if para is None:
                standalone.append(_TextRef(element))
            else:
                by_para.setdefault(id(para), []).append(_TextRef(element))
        for attr_tag, attr_name in _TEXT_ATTRS:
            if tag == attr_tag and element.get(attr_name):
                para = _closest_paragraph(element)
                ref = _TextRef(element, is_attribute=True, attr_name=attr_name)
                if para is None:
                    standalone.append(ref)
                else:
                    by_para.setdefault(id(para), []).append(ref)
        # Relationship Target attributes: external URLs / mailto: (§6.2 item 3).
        if tag == _RELS_TAG and element.get("TargetMode") == "External":
            value = element.get("Target", "")
            if value:
                standalone.append(_TextRef(element, is_attribute=True, attr_name="Target"))

    # Keep paragraph groups in document order (dict preserves insertion order,
    # which follows root.iter() document order).
    for refs in by_para.values():
        if refs:
            result.append(_Container(refs=refs, part_name=part_name, category=category))
    if standalone:
        result.append(_Container(refs=standalone, part_name=part_name, category=category))
    return result


def _closest_paragraph(element: etree._Element) -> etree._Element | None:
    current = element.getparent()
    while current is not None:
        if current.tag == _PARAGRAPH_TAG:
            return current
        current = current.getparent()
    return None


def _rewrite_container(container: _Container, spans: list[tuple[int, int, str]]) -> None:
    """
    Rewrite text refs back-to-front so earlier offsets stay valid.

    `spans` are (start, end, replacement) over the concatenated container text.
    A span covering several refs emits the replacement once into the ref holding
    its start and removes only the covered slices from continuation refs.
    """
    for start, end, replacement in sorted(spans, reverse=True):
        bounds: list[tuple[int, int]] = []
        cursor = 0
        for ref in container.refs:
            length = len(ref.get())
            bounds.append((cursor, cursor + length))
            cursor += length
        for index, (ref_start, ref_end) in enumerate(bounds):
            if start >= ref_end or end <= ref_start:
                continue
            left = max(start, ref_start) - ref_start
            right = min(end, ref_end) - ref_start
            ref = container.refs[index]
            value = ref.get()
            if start >= ref_start and start < ref_end:
                ref.set(value[:left] + replacement + value[right:])
            else:
                ref.set(value[:left] + value[right:])


def transform_package(
    data: bytes,
    *,
    mode: str,
    masker: Any = None,  # noqa: ANN401
    keys: Any = None,  # noqa: ANN401
    placeholders: Any = None,  # noqa: ANN401
) -> tuple[bytes, list[Any], list[str], CoverageReport]:
    """
    Mask or restore every supported text-bearing part.

    Other ZIP entries are copied byte-for-byte. Raises `OoxmlPackageError` for
    unsafe/uninspectable content.
    """
    parts, coverage = build_inventory(data)
    if coverage.rejected_parts:
        msg = "Документ содержит неподдерживаемое вложенное содержимое, которое нельзя безопасно обработать."
        raise OoxmlPackageError(msg)

    with zipfile.ZipFile(io.BytesIO(data)) as source:
        infos = {info.filename: info for info in source.infolist() if not info.is_dir()}
        edited: dict[str, bytes] = {}
        all_entities: list[Any] = []
        used_placeholders: set[str] = set()

        for part in parts:
            if not part.is_xml or part.category in ("content_types", "media", "embedded_object", "other_binary"):
                continue
            root = _secure_parse(part.data)
            containers = _containers(root, part.name, part.category)
            if not containers:
                continue
            modified = False
            for container in containers:
                text = container.text()
                if not text.strip():
                    continue
                local: list[tuple[int, int, str]] = []
                if mode == "mask":
                    for item in masker.detect(text):
                        marker = keys.placeholder(item.label, item.text)
                        local.append((item.start, item.end, marker))
                        all_entities.append(
                            {
                                "label": item.label,
                                "text": item.text,
                                "start": item.start,
                                "end": item.end,
                                "score": item.score,
                            }
                        )
                else:
                    for placeholder, value in placeholders.items():
                        cursor = 0
                        while True:
                            found = text.find(placeholder, cursor)
                            if found < 0:
                                break
                            local.append((found, found + len(placeholder), value))
                            used_placeholders.add(placeholder)
                            cursor = found + len(placeholder)
                if local:
                    _rewrite_container(container, local)
                    modified = True
            if modified:
                edited[part.name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                coverage.processed_parts.append(part.category)
                coverage.processed.append(ProcessedPart(part.name, part.category, len(containers)))

        unused = (
            [value for placeholder, value in placeholders.items() if placeholder not in used_placeholders]
            if mode == "unmask"
            else []
        )

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for name in sorted(infos):
                info = infos[name]
                payload = edited.get(name, source.read(name))
                # Preserve the original ZipInfo (timestamps, external attrs,
                # compression type) so unchanged entries stay semantically equal.
                clone = zipfile.ZipInfo(name, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                clone.internal_attr = info.internal_attr
                clone.create_system = info.create_system
                target.writestr(clone, payload)
        return out.getvalue(), all_entities, unused, coverage
