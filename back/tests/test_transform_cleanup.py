"""DM-1 failure-path cleanup: no partial DOCX after a failed transform.

Synthetic OPC fixtures only; no real personal data is used. The collision is
fail-closed (422 `marker_collision` on sync routes, a safe failed job on the
durable path) and no temp output, published artifact or cache entry survives.
"""

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from docx import Document as DocxDocument
from httpx2 import ASGITransport, AsyncClient
from ooxml_fixtures import _build_package

from src.jobs.operations import execute
from src.jobs.storage import ArtifactStore
from src.main import app
from src.masking.docx_service import mask_docx_in_memory
from src.masking.mapping import MarkerCollisionError, MaskKeys, parse_keys_yaml, placeholders_from_document
from src.masking.ooxml import package
from src.masking.ooxml.package import OoxmlPackageError, transform_package_to_file
from src.masking.rule_based import RuleBasedMasker
from src.masking.schemas import MaskRequest
from src.masking.service import mask_document

_PERSON = "Иван Петров"
_COLLIDING = f"{_PERSON} и [PERSON_1]"
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx(text: str) -> bytes:
    doc = DocxDocument()
    doc.add_paragraph(text)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _temp_files(directory: Path) -> list[Path]:
    return list(directory.glob(".dm-transform-*"))


# --- sync error contract: text and DOCX --------------------------------------


def test_text_collision_is_a_422_without_echoing_source() -> None:
    with pytest.raises(MarkerCollisionError) as raised:
        mask_document(MaskRequest(text=_COLLIDING))
    error = raised.value
    assert error.status_code == 422
    assert error.code == "marker_collision"
    assert "[PERSON_1]" in str(error)
    assert _PERSON not in str(error)


def test_docx_collision_is_a_422_without_echoing_source() -> None:
    with pytest.raises(MarkerCollisionError) as raised:
        mask_docx_in_memory(_docx(_COLLIDING), RuleBasedMasker.for_labels(["person"]))
    error = raised.value
    assert error.status_code == 422
    assert error.code == "marker_collision"
    assert _PERSON not in str(error)


async def test_api_text_collision_is_422_and_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.masking import router

    puts: list[bytes] = []

    async def record_put(_key: str, data: bytes) -> None:
        puts.append(data)

    monkeypatch.setattr(router, "_cache_put_async", record_put)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.post("/api/mask", json={"text": _COLLIDING}),
            await client.post("/api/mask", json={"text": _COLLIDING}),
        ]

    for response in responses:
        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"code", "detail", "request_id"}
        assert body["code"] == "marker_collision"
        assert response.headers["X-Request-ID"] == body["request_id"]
        assert _PERSON not in json.dumps(body, ensure_ascii=False)
    assert puts == []  # a failed mask never publishes a cache entry


async def test_api_docx_collision_is_422_with_no_artifact_or_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.masking import router

    writes: list[bytes] = []

    async def record_cache(_key: str, archive: bytes, _headers: dict[str, str]) -> None:
        writes.append(archive)

    monkeypatch.setattr(router, "_cache_file_async", record_cache)

    def upload() -> dict[str, tuple[str, bytes, str]]:
        return {"file": ("collision.docx", _docx(_COLLIDING), _DOCX_MEDIA_TYPE)}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.post("/api/mask-file", files=upload()),
            await client.post("/api/mask-file", files=upload()),
        ]

    for response in responses:
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "marker_collision"
        assert response.headers["X-Request-ID"] == body["request_id"]
        assert not response.headers["content-type"].startswith("application/zip")
        assert _PERSON not in json.dumps(body, ensure_ascii=False)
    assert writes == []  # no result ZIP, so nothing to download or hit later


# --- path-based transform: no partial target and no leftover temp ------------


def test_path_transform_collision_removes_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(_COLLIDING))
    target = tmp_path / "out.docx"

    with pytest.raises(MarkerCollisionError):
        transform_package_to_file(
            source,
            target,
            mode="mask",
            masker=RuleBasedMasker.for_labels(["person"]),
            keys=MaskKeys(),
        )
    assert not target.exists()
    assert _temp_files(tmp_path) == []


def test_path_transform_cancellation_removes_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(_COLLIDING))
    target = tmp_path / "out.docx"
    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        if calls >= 4:  # at least one part has already been streamed
            msg = "cancelled"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="cancelled"):
        transform_package_to_file(
            source,
            target,
            mode="mask",
            masker=RuleBasedMasker.for_labels(["person"]),
            keys=MaskKeys(),
            checkpoint=stop,
        )
    assert not target.exists()
    assert _temp_files(tmp_path) == []


def test_path_transform_parse_error_removes_partial_output(tmp_path: Path) -> None:
    # A broken part sorted after document.xml is reached only after output began.
    source = tmp_path / "source.docx"
    source.write_bytes(_build_package(extra={"word/zzz_broken.xml": b"<not-closed"}))
    target = tmp_path / "out.docx"

    with pytest.raises(OoxmlPackageError):
        transform_package_to_file(
            source,
            target,
            mode="mask",
            masker=RuleBasedMasker(),
            keys=MaskKeys(),
        )
    assert not target.exists()
    assert _temp_files(tmp_path) == []


def test_path_transform_serialization_error_removes_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(_PERSON))  # build the fixture before patching lxml
    target = tmp_path / "out.docx"

    def broken_tostring(*_args: Any, **_kwargs: Any) -> bytes:  # noqa: ANN401
        msg = "serialization failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(package.etree, "tostring", broken_tostring)

    with pytest.raises(RuntimeError, match="serialization failed"):
        transform_package_to_file(
            source,
            target,
            mode="mask",
            masker=RuleBasedMasker.for_labels(["person"]),
            keys=MaskKeys(),
        )
    assert not target.exists()
    assert _temp_files(tmp_path) == []


def test_path_failure_leaves_a_preexisting_artifact_untouched(tmp_path: Path) -> None:
    """Cleanup is scoped to output we created — never another job's artifact."""
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(_COLLIDING))
    target = tmp_path / "published.docx"
    published_bytes = b"already-published-artifact"
    target.write_bytes(published_bytes)

    with pytest.raises(MarkerCollisionError):
        transform_package_to_file(
            source,
            target,
            mode="mask",
            masker=RuleBasedMasker.for_labels(["person"]),
            keys=MaskKeys(),
        )
    assert target.read_bytes() == published_bytes
    assert _temp_files(tmp_path) == []


# --- worker execute(): temp output is owned by the executing attempt ---------


def _job_store(tmp_path: Path, *, kind: bytes, source: bytes, keys: bytes | None = None) -> tuple[ArtifactStore, str]:
    store = ArtifactStore(tmp_path)
    job_id = str(uuid.uuid4())
    store.write(job_id, "kind", kind)
    store.write(job_id, "name", b"doc.docx")
    store.write(job_id, "input", source)
    if keys is not None:
        store.write(job_id, "keys", keys)
    return store, job_id


def test_job_collision_leaves_no_temp_or_result_artifact(tmp_path: Path) -> None:
    store, job_id = _job_store(tmp_path, kind=b"mask_file", source=_docx(_COLLIDING))

    with pytest.raises(MarkerCollisionError):
        execute(store, job_id, progress=lambda _phase, _pct: None)

    assert _temp_files(store.root) == []
    assert not store.exists(job_id, "result")
    assert list(store.root.glob(f"{job_id}.result.*")) == []


def test_job_cancellation_during_transform_leaves_no_temp(tmp_path: Path) -> None:
    from src.jobs.worker import CancelledError

    store, job_id = _job_store(tmp_path, kind=b"mask_file", source=_docx(_COLLIDING))
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls >= 4:  # after kind read and post-validation check, inside the transform
            raise CancelledError

    with pytest.raises(CancelledError):
        execute(store, job_id, progress=lambda _phase, _pct: None, checkpoint=cancel)

    assert _temp_files(store.root) == []
    assert list(store.root.glob(f"{job_id}.result.*")) == []


def test_job_worker_failure_during_unmask_leaves_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.jobs import operations

    masked, _entities, keys, _coverage = mask_docx_in_memory(_docx(_PERSON), RuleBasedMasker.for_labels(["person"]))
    store, job_id = _job_store(
        tmp_path,
        kind=b"unmask_file",
        source=masked,
        keys=keys.to_yaml().encode("utf-8"),
    )

    def failing_unmask(
        _source_path: Path,
        target_path: Path,
        _placeholders: object,
        *,
        checkpoint: Any = None,  # noqa: ANN401
    ) -> tuple[list[str], object]:
        target_path.write_bytes(b"partial output")
        msg = "worker failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(operations, "unmask_docx_to_file", failing_unmask)
    with pytest.raises(RuntimeError, match="worker failure"):
        execute(store, job_id, progress=lambda _phase, _pct: None)

    assert _temp_files(store.root) == []
    assert list(store.root.glob(f"{job_id}.result.*")) == []


def test_job_success_hands_temp_output_to_the_caller(tmp_path: Path) -> None:
    store, job_id = _job_store(tmp_path, kind=b"mask_file", source=_docx(_PERSON))

    entries = execute(store, job_id, progress=lambda _phase, _pct: None)
    owned = [value for value in entries.values() if isinstance(value, Path)]
    assert len(owned) == 1
    assert owned[0].exists()
    assert owned[0].name.startswith(".dm-transform-")
    # Simulate worker.run_one's finally: only the owner cleans the output.
    owned[0].unlink()
    assert _temp_files(store.root) == []


def test_successful_docx_roundtrip_still_works(tmp_path: Path) -> None:
    """Cleanup never changes the key format or the mask -> keys -> unmask path."""
    masked, _entities, keys, _coverage = mask_docx_in_memory(_docx(_PERSON), RuleBasedMasker.for_labels(["person"]))
    source = tmp_path / "source.docx"
    source.write_bytes(masked)
    target = tmp_path / "restored.docx"

    assert keys.to_yaml().startswith("Профиль: russian-default")
    assert "версия" not in keys.to_yaml().lower()
    placeholders = placeholders_from_document(parse_keys_yaml(keys.to_yaml()))
    assert placeholders == {"[PERSON_1]": _PERSON}
    transform_package_to_file(
        source,
        target,
        mode="unmask",
        placeholders=placeholders,
    )
    with zipfile.ZipFile(target) as archive:
        assert _PERSON.encode("utf-8") in b"".join(archive.read(name) for name in archive.namelist())
