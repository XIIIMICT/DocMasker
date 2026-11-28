"""
Versioned result cache (Phase 3, ADR-0003 / brief §4).

Valkey holds only compact JSON entries pointing to immutable result artifacts
on the private volume; large DOCX/ZIP payloads never enter Valkey. Keys are
HMAC-SHA256 digests over a canonical fingerprint of every output-affecting
input plus version/config digests, so no raw hashes or PII are guessable or
logged. Cache outages degrade to misses, never to failed operations.

Stable import facade: ``keys`` owns fingerprints and the entry schema,
``artifacts`` owns the spool and GC selection, ``result`` owns the Valkey
wrapper. Keep every re-export here so existing imports keep working.
"""

from .artifacts import CacheArtifactStore, CacheGcReport, cache_gc_stats
from .keys import (
    CACHE_ENTRY_VERSION,
    CACHE_KEY_SCHEMA_VERSION,
    ENGINE_COMPAT_VERSION,
    CacheEntry,
    CacheFingerprint,
    build_cache_key,
    canonical_json,
    fingerprint_for,
    sha256_hex,
)
from .result import GC_GRACE_SECONDS, GC_MIN_RETENTION_SECONDS, ResultCache

__all__ = [
    "CACHE_ENTRY_VERSION",
    "CACHE_KEY_SCHEMA_VERSION",
    "ENGINE_COMPAT_VERSION",
    "GC_GRACE_SECONDS",
    "GC_MIN_RETENTION_SECONDS",
    "CacheArtifactStore",
    "CacheEntry",
    "CacheFingerprint",
    "CacheGcReport",
    "ResultCache",
    "build_cache_key",
    "cache_gc_stats",
    "canonical_json",
    "fingerprint_for",
    "sha256_hex",
]
