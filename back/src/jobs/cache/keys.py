"""
Versioned cache keys, fingerprints and the compact Valkey entry schema.

Keys are HMAC-SHA256 digests over a canonical fingerprint of every
output-affecting input plus version/config digests, so no raw hashes or PII are
guessable or logged. This module is independent of artifact storage and Valkey.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings

#: Cache key / entry schema. Bump CACHE_NAMESPACE_VERSION when either changes.
#: 2: the canonical config snapshot digest is always in the key, even when a
#: manual MASKING_CONFIG_DIGEST override is configured.
CACHE_KEY_SCHEMA_VERSION = 2
CACHE_ENTRY_VERSION = 1
#: Increment these release-checklist constants when the corresponding behavior
#: changes output (brief §4.3). The values live in Settings so operators can
#: bump them via env without a code deploy; these defaults document the floor.
ENGINE_COMPAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class CacheFingerprint:
    """Every output-affecting input of one operation (§4.2)."""

    operation: str
    input_hash: str
    keys_hash: str | None = None
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Compact Valkey value: a reference to an immutable artifact."""

    artifact_id: str
    content_hash: str
    size: int
    content_type: str
    manifest_version: int
    entry_version: int = CACHE_ENTRY_VERSION


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:  # noqa: ANN401 (arbitrary JSON structure)
    """Stable serialization: sorted keys, compact separators, UTF-8."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_cache_key(settings: Settings, fingerprint: CacheFingerprint) -> str:
    """
    `docmasker:result:v{VERSION}:{operation}:{HMAC(fingerprint)}`.

    The fingerprint includes the exact input/keys hashes, a normalized output
    name when archive entry names depend on it, and every version/digest that
    can change the result. HMAC with a stable secret prevents offline guessing
    of predictable PII inputs and keeps source hashes out of key names.
    """
    from src.masking.config import config_digest

    # The loaded snapshot always participates: a manual override can only add
    # invalidation, never disable it (e.g. MASKING_CONFIG_DIGEST=none).
    digest_input = config_digest()
    payload = {
        "operation": fingerprint.operation,
        "input": fingerprint.input_hash,
        "keys": fingerprint.keys_hash,
        "output_name": fingerprint.output_name,
        "ruleset": settings.masking_ruleset_version,
        "config_digest": digest_input,
        "config_digest_override": settings.masking_config_digest,
        "manifest": settings.result_manifest_version,
        "keys_format": settings.keys_format_version,
        "docx_transformer": settings.docx_transformer_version,
        "engine": ENGINE_COMPAT_VERSION,
        "entry": CACHE_ENTRY_VERSION,
        "key_schema": CACHE_KEY_SCHEMA_VERSION,
    }
    digest = hmac.new(
        settings.cache_key_secret.encode("utf-8"),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"docmasker:result:v{settings.cache_namespace_version}:{fingerprint.operation}:{digest}"


def fingerprint_for(
    operation: str,
    *,
    input_bytes: bytes | None = None,
    input_digest: str | None = None,
    keys_bytes: bytes | None = None,
    filename: str = "",
) -> CacheFingerprint:
    """
    Build the canonical fingerprint for one operation (§4.2).

    `input_digest` lets the streaming job-submit path reuse a SHA-256 computed
    from a staged file without ever materialising the whole upload in RAM
    (Phase 6 §8.5); the in-memory path passes `input_bytes` as before.
    """
    if input_digest is None:
        if input_bytes is None:
            msg = "Either input_bytes or input_digest is required."
            raise ValueError(msg)
        input_digest = sha256_hex(input_bytes)
    return CacheFingerprint(
        operation=operation,
        input_hash=input_digest,
        keys_hash=sha256_hex(keys_bytes) if keys_bytes is not None else None,
        # Archive entry names depend on the filename stem, so it is part of the
        # fingerprint (§4.2 / test 8). Text operations ignore it.
        output_name=Path(filename).stem if operation.endswith("_file") else None,
    )
