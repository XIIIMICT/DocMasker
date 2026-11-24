"""
Execute one job's operation over its spool artifacts (ADR-0002).

This is the single place that reuses the existing masking services for the
worker: no detection logic is duplicated, and the sync endpoints keep their
thin orchestration. Each call runs in a worker thread; the masker itself is a
process-wide cached singleton built from one immutable config snapshot.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.masking.docx_service import mask_docx_in_memory, unmask_docx_in_memory
from src.masking.docx_validation import upload_limits, validate_docx_package
from src.masking.mapping import placeholders_from_document, validate_keys_yaml
from src.masking.rule_based import get_masker
from src.masking.schemas import MaskRequest, UnmaskRequest
from src.masking.service import mask_document, mask_stats, unmask_document

from .models import JobPhase
from .storage import ArtifactStore


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
) -> dict[str, bytes]:
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
            "kind": kind,
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
        return {"result.json": json.dumps({"text": restored, "kind": kind}, ensure_ascii=False).encode("utf-8")}

    if kind == "mask_file":
        data = safe_read("input")
        try:
            validate_docx_package(data, upload_limits())
        except Exception as error:
            raise JobExecutionError("invalid_package") from error
        check()
        progress("detection", 40)
        masked_bytes, _entities, keys = mask_docx_in_memory(data, masker)
        check()
        progress("rewrite", 80)
        name = safe_read("name").decode("utf-8") or "document.docx"
        base = Path(name).stem
        progress("archive", 95)
        return {
            f"{base} (замаскированный).docx": masked_bytes,
            f"{base} (ключи демаскировки).yaml": keys.to_yaml().encode("utf-8"),
        }

    if kind == "unmask_file":
        data = safe_read("input")
        try:
            validate_docx_package(data, upload_limits())
        except Exception as error:
            raise JobExecutionError("invalid_package") from error
        keys_yaml = safe_read("keys").decode("utf-8")
        try:
            placeholders = placeholders_from_document(validate_keys_yaml(keys_yaml))
        except Exception as error:
            raise JobExecutionError("invalid_keys") from error
        check()
        progress("detection", 40)
        restored, unused = unmask_docx_in_memory(data, placeholders)
        check()
        progress("rewrite", 80)
        name = safe_read("name").decode("utf-8") or "document.docx"
        base = Path(name).stem.removesuffix(" (замаскированный)")
        progress("archive", 95)
        return {
            f"{base} (демаскированный).docx": restored,
            f"{base} (отчёт о демаскировке).json": json.dumps(
                {"unused_keys": unused}, ensure_ascii=False, indent=2
            ).encode("utf-8"),
        }

    raise JobExecutionError("unknown_kind")
