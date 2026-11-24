# ADR-0004: OPC-aware OOXML transformer

- Status: **accepted** (Phase 5, 2026-11-24)
- Implements: implementation brief §6 (namespace-aware DOCX coverage) and §7
  (one versioned result manifest)
- Related: ADR-0001 (keys format compatibility), `docs/DOCX_SUPPORT.md`,
  `docs/API.md`

## Context

The previous masking path used python-docx run-level traversal. It silently
skipped text-bearing parts (footnotes, comments, tracked deletions, text boxes,
metadata, hyperlink targets), depended on hardcoded `w:` prefixes, emitted an
`X-Docx-Unsupported-Parts` warning for partial results, and could not guarantee
that an entity spanning runs became one placeholder. Task branches before
Phase 5 had added individual header/footer variants ad hoc.

## Decision

- Discover parts from the OPC package itself: `[Content_Types].xml` plus
  relationship files, indexed by QName/URI. Never hardcode namespace prefixes.
- Parse XML with `defusedxml.lxml` (`forbid_dtd`, `forbid_entities`, no
  network), group text nodes by their nearest paragraph container, rewrite
  slices back-to-front so one entity spanning several nodes yields one marker.
- Re-serialize only modified ZIP entries; copy every other entry byte-for-byte
  (images, styles, numbering). Process all supported text-bearing parts in the
  same way for sync and job paths.
- Fail closed with a 422 on uninspectable PII-capable content: embedded OLE /
  `*.bin`, unsafe or malformed XML, oversized parts. Raster image pixels are a
  documented non-text limitation, not a blocker. No warning-only "best effort"
  result.
- Emit one versioned `manifest.json` (statistics, coverage, input hash, output
  names, versions, cache) from the same serializer for sync and job file
  results. No semantic `X-*` headers. The frontend requires and validates the
  manifest.

## Consequences

- Genuine coverage is broad and namespace-independent; missing a new part is a
  test-fixture change, not a prefix guess.
- Unchanged binary parts stay identical; DOCX formatting is preserved.
- Callers that relied on warning headers must use the manifest instead: this
  is a deliberate breaking change of the file contract.
- Part-level XML still needs DOM memory; we do not claim a fully streaming DOCX
  pipeline (`docs/PERFORMANCE.md`).
- `DOCX_TRANSFORMER_VERSION` / `RESULT_MANIFEST_VERSION` must be bumped when
  output semantics change, or cached results become stale. Bumped to **2** when
  standalone docProps/relationship fields became separate containers; without
  the bump the old cross-field key is served from Valkey.
- Verification: `tests/test_ooxml.py` synthetic OPC fixtures (custom prefixes,
  >64 KiB text, shared parts, round-trip, strict rejection),
  `tests/test_mask_file.py`, `tests/test_unmask.py`, frontend e2e manifest
  validation.
