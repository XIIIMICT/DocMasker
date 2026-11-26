"""
Execute one job's operation over its spool artifacts (ADR-0002).

This is the single place that reuses the existing masking services for the
worker: no detection logic is duplicated, and the sync endpoints keep their
thin orchestration. Each call runs in a worker thread; the masker itself is a
process-wide cached singleton built from one immutable config snapshot.
"""

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.masking.archives import safe_stem
from src.masking.config import get_config
from src.masking.docx_service import mask_docx_to_file, unmask_docx_to_file
from src.masking.docx_validation import upload_limits, validate_docx_package_path
from src.masking.mapping import placeholders_from_document, validate_keys_yaml
from src.masking.ooxml.manifest import (
    MANIFEST_NAME,
    build_mask_manifest,
    build_unmask_manifest,
)
from src.masking.rule_based import get_masker
from src.masking.schemas import MaskRequest, UnmaskRequest
from src.masking.service import mask_document, mask_stats, unmask_document

from .models import JobPhase
from .storage import ArtifactStore


def _stream_digest(path: Path) -> tuple[str, int]:
    """SHA-256 and size from a streamed file (never materialises the input)."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _temp_output(store: ArtifactStore) -> Path:
    """Create a bounded temporary output path inside the private spool."""
    handle, name = tempfile.mkstemp(prefix=".dm-transform-", dir=str(store.root))
    os.close(handle)
    Path(name).unlink()
    return Path(name)


def _manifest_ctx() -> dict[str, Any]:
    """Version/digest fields shared by sync and job manifests (§7.2)."""
    settings = get_settings()
    return {
        "masking_ruleset_version": settings.masking_ruleset_version,
        "exception_config_digest": get_config().digest,
        "namespace_version": settings.cache_namespace_version,
    }


class JobExecutionError(Exception):
    """A safe, classified failure (no input values in the message)."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def execute(
    store: ArtifactStore,
    job_id: str,
    *,
    progress: Callable[[JobPhase, int], None],
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, bytes | Path]:
    """
    Run the operation for `job_id` and return the result-archive entries.

    `progress` reports a fenced phase/progress; `checkpoint` is a cooperative
    cancellation hook called before each expensive stage (§3.7). Every failure
    is raised as a safe `JobExecutionError` — no input values, keys or
    exception text are persisted or logged.
    """
    check = checkpoint or (lambda: None)

    def safe_read(suffix: str) -> bytes:
        check()
        try:
            return store.read(job_id, suffix)
        except Exception as error:
            raise JobExecutionError("artifact_io", retryable=True) from error

    kind = safe_read("kind").decode("utf-8")
    masker = get_masker()

    progress("validation", 10)

    if kind == "mask_text":
        text = safe_read("input").decode("utf-8")
        request = MaskRequest(text=text)
        masked, entities, keys = mask_document(request)
        check()
        progress("detection", 60)
        progress("rewrite", 85)
        payload: dict[str, Any] = {
            "text": masked,
            "keys": keys.to_yaml(),
            "stats": mask_stats(entities).model_dump(),
        }
        progress("archive", 95)
        return {"result.json": json.dumps(payload, ensure_ascii=False).encode("utf-8")}

    if kind == "unmask_text":
        text = safe_read("input").decode("utf-8")
        keys_yaml = safe_read("keys").decode("utf-8")
        try:
            validate_keys_yaml(keys_yaml)
        except Exception as error:
            raise JobExecutionError("invalid_keys") from error
        request = UnmaskRequest(text=text, keys=keys_yaml)
        restored = unmask_document(request)
        check()
        progress("rewrite", 85)
        return {"result.json": json.dumps({"text": restored}, ensure_ascii=False).encode("utf-8")}

    if kind == "mask_file":
        source_path = store.path_for(job_id, "input")
        try:
            validate_docx_package_path(source_path, upload_limits())
        except Exception as error:
            raise JobExecutionError("invalid_package") from error
        check()
        progress("detection", 40)
        output_path = _temp_output(store)
        stats, keys, coverage = mask_docx_to_file(source_path, output_path, masker, checkpoint=check)
        check()
        progress("rewrite", 80)
        name = safe_read("name").decode("utf-8") or "document.docx"
        base = safe_stem(name)
        document_name = f"{base} (замаскированный).docx"
        keys_name = f"{base} (ключи демаскировки).yaml"
        ctx = _manifest_ctx()
        manifest = build_mask_manifest(
            source_bytes=b"",
            input_digest=_stream_digest(source_path),
            document_name=document_name,
            keys_name=keys_name,
            stats=stats.as_manifest_stats(),
            coverage=coverage.as_manifest(),
            cache_hit=False,
            namespace_version=ctx["namespace_version"],
            masking_ruleset_version=ctx["masking_ruleset_version"],
            exception_config_digest=ctx["exception_config_digest"],
        )
        progress("archive", 95)
        return {
            document_name: output_path,
            keys_name: keys.to_yaml().encode("utf-8"),
            MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        }

    if kind == "unmask_file":
        source_path = store.path_for(job_id, "input")
        try:
            validate_docx_package_path(source_path, upload_limits())
        except Exception as error:
            raise JobExecutionError("invalid_package") from error
        keys_yaml = safe_read("keys").decode("utf-8")
        try:
            placeholders = placeholders_from_document(validate_keys_yaml(keys_yaml))
        except Exception as error:
            raise JobExecutionError("invalid_keys") from error
        check()
        progress("detection", 40)
        output_path = _temp_output(store)
        unused, coverage = unmask_docx_to_file(source_path, output_path, placeholders, checkpoint=check)
        check()
        progress("rewrite", 80)
        name = safe_read("name").decode("utf-8") or "document.docx"
        base = safe_stem(name).removesuffix(" (замаскированный)")
        document_name = f"{base} (демаскированный).docx"
        ctx = _manifest_ctx()
        manifest = build_unmask_manifest(
            source_bytes=b"",
            input_digest=_stream_digest(source_path),
            document_name=document_name,
            unused_keys=unused,
            coverage=coverage.as_manifest(),
            cache_hit=False,
            namespace_version=ctx["namespace_version"],
            masking_ruleset_version=ctx["masking_ruleset_version"],
            exception_config_digest=ctx["exception_config_digest"],
        )
        progress("archive", 95)
        return {
            document_name: output_path,
            MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        }

    raise JobExecutionError("unknown_kind")
