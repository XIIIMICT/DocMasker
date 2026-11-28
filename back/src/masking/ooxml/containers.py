"""
Namespace-aware editable text containers for OOXML parts (§6.2–6.4).

A container groups ordered text references by their closest logical paragraph
(or is one standalone metadata field / external relationship target). Detectors
and marker replacement operate on the concatenated container text; a span
covering several text nodes is written back as one placeholder.

The URI constants below mirror `inventory.py`; they are deliberately local so
this module has no dependency on the inventory or the package transform and the
import graph stays acyclic. Keep the two sets in sync when a namespace changes.
"""

from dataclasses import dataclass

# lxml ships no PEP 561 type information; a dynamic import keeps static type
# checkers quiet while still using lxml's element helpers at runtime.
etree = __import__("lxml.etree", fromlist=["etree"])

__all__ = [
    "_Container",
    "_TextRef",
    "_closest_paragraph",
    "_containers",
    "_generic_containers",
    "_rewrite_container",
]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
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
    # Standalone refs are INDEPENDENT values (docProps fields, external
    # relationship targets, paragraph-less text). Concatenating them into one
    # container would create false entities that span field boundaries — e.g.
    # dc:creator «Иван Петров» + cp:lastModifiedBy «Иван Петров» becomes
    # «Иван ПетровИван», producing a nonsensical key. One ref = one container.
    for ref in standalone:
        result.append(_Container(refs=[ref], part_name=part_name, category=category))
    return result


def _generic_containers(root: etree._Element, part_name: str, category: str) -> list[_Container]:
    """
    Text nodes of an unknown XML part (e.g. customXml/item1.xml).

    Only element text is touched — attributes and structure are preserved.
    """
    refs = [
        _TextRef(element)
        for element in root.iter()
        if isinstance(element.tag, str) and element.text and element.text.strip()
    ]
    return [_Container(refs=refs, part_name=part_name, category=category)] if refs else []


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
