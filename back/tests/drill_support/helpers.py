"""Shared helpers for the disposable operational drill harness.

Every helper here takes an explicit PostgreSQL URL and artifact directory. It
never reads the root ``.env``, never mounts a shared volume and never deletes
anything outside the caller-provided spool. Production code is exercised as-is;
failures are injected by swapping module attributes inside the harness only.
"""

from __future__ import annotations

import hashlib
import io
import platform
import shutil
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.config import Settings
from src.jobs.repository import JobRepository  # noqa: TC001 - preserve the original harness module namespace
from src.jobs.storage import ArtifactStore  # noqa: TC001 - preserve the original harness module namespace

if TYPE_CHECKING:
    from src.jobs.models import Claim, JobKind


@dataclass(slots=True)
class DrillReport:
    """JSON-safe collection of measured drill outcomes (no PII, no URLs)."""

    cases: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, **facts: object) -> None:
        self.cases.append({"name": name, **facts})

    def as_dict(self) -> dict[str, object]:
        return {"cases": self.cases}


class NoCache:
    """Offline cache double: the drills never contact Valkey."""

    def get_to_path(self, _key: str, _target: Path) -> bool:
        return False

    def put_from_path(self, _key: str, _source: Path) -> None:
        return None


def drill_settings(pg_url: str, jobs_dir: Path, **overrides: object) -> Settings:
    """Build settings pinned to a disposable database and spool."""
    values: dict[str, object] = {
        "database_url": pg_url,
        "jobs_dir": str(jobs_dir),
        "jobs_concurrency": 2,
        "jobs_max_attempts": 3,
        "jobs_lease_seconds": 30.0,
        "jobs_poll_seconds": 0.01,
        "jobs_ttl_seconds": 3600.0,
        "cache_enabled": False,
        "cache_key_secret": "",
        "worker_metrics_host": "127.0.0.1",
        "worker_metrics_port": 18002,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _cache_key(kind: str, data: bytes, filename: str) -> str:
    """Synthetic opaque key; caching is disabled in the drills."""
    return f"drill:{hashlib.sha256(data + filename.encode('utf-8') + uuid.uuid4().bytes).hexdigest()}:{kind}"


def enqueue_direct(
    settings: Settings,
    repo: JobRepository,
    store: ArtifactStore,
    kind: JobKind,
    *,
    data: bytes,
    filename: str = "",
    keys: bytes | None = None,
) -> str:
    """Drive the production staging -> queued repository methods explicitly."""
    staged, created = repo.create_staging(
        kind,
        max_active=settings.jobs_max_active,
        max_attempts=settings.jobs_max_attempts,
        cache_key=_cache_key(kind, data, filename),
    )
    if not created:
        msg = "Drill inputs must be unique; staging dedupe attached to an existing job."
        raise RuntimeError(msg)
    try:
        store.write(staged.id, "name", filename.encode("utf-8"))
        store.write(staged.id, "kind", kind.encode("ascii"))
        store.write(staged.id, "input", data)
        if keys is not None:
            store.write(staged.id, "keys", keys)
    except Exception:
        repo.abort_staging(staged.id, "staging_failed")
        store.delete(staged.id)
        raise
    repo.publish_staging(
        staged.id,
        input_artifact="input",
        keys_artifact="keys" if keys is not None else None,
        ttl_seconds=settings.jobs_ttl_seconds,
    )
    return staged.id


def build_docx(paragraphs: int, marker: str) -> bytes:
    """Deterministic synthetic DOCX with PII-shaped patterns (no real data)."""
    import io as _io

    from docx import Document

    doc = Document()
    for index in range(paragraphs):
        doc.add_paragraph(
            f"Подписант {index}: Иван Петров, телефон +7 (900) 123-45-{index % 100:02d}, "
            f"почта user{index}@example.com,marker={marker}."
        )
    buffer = _io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def synthetic_text(repeats: int, marker: str) -> bytes:
    """Synthetic Russian text containing only fabricated PII-shaped values."""
    return " ".join(f"Иван Петров, ivan{i}@example.com marker={marker}" for i in range(repeats)).encode("utf-8")


def result_integrity(store: ArtifactStore, job_id: str, key: str) -> dict[str, Any]:
    """Verify the exact DB-referenced artifact: bytes, ZIP structure, hash."""
    path = store.path_for_result(key)
    raw = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        corrupt = archive.testzip()
        entry_count = len(archive.namelist())
    return {
        "job_id": job_id,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "zip_ok": corrupt is None and entry_count > 0,
        "entry_count": entry_count,
    }


def _sql_expire_staging(repo: JobRepository, job_id: str) -> None:
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET staging_until = clock_timestamp() - interval '2 seconds' WHERE id = :id"),
            {"id": job_id},
        )


def _sql_expire_lease(repo: JobRepository, job_id: str) -> None:
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET lease_until = clock_timestamp() - interval '2 seconds' WHERE id = :id"),
            {"id": job_id},
        )


def _sql_expire_terminal(repo: JobRepository, job_id: str) -> None:
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET result_expires_at = clock_timestamp() - interval '2 seconds' WHERE id = :id"),
            {"id": job_id},
        )


def _spool_audit(store: ArtifactStore, repo: JobRepository) -> dict[str, int]:
    """Count referenced-but-missing result bytes and unreferenced result files."""
    with repo._engine.connect() as connection:
        rows = connection.execute(text("SELECT result_artifact FROM jobs WHERE result_artifact IS NOT NULL")).all()
    referenced = {str(row[0]) for row in rows}
    files = {path.name for path in store.root.glob("*.result.*") if path.is_file()}
    return {
        "missing_artifacts": len(referenced - files),
        "orphan_artifacts": len(files - referenced),
        "referenced_artifacts": len(referenced),
        "result_files": len(files),
    }


def _claim(repo: JobRepository, settings: Settings) -> tuple[str, Claim]:
    claimed = repo.claim_with_owner(
        lease_seconds=settings.jobs_lease_seconds,
        concurrency=settings.jobs_concurrency,
        worker_id=str(uuid.uuid4()),
    )
    if claimed is None:
        msg = "Expected a claimable queued job in the drill."
        raise RuntimeError(msg)
    return claimed[0].id, claimed[1]


def _peak_rss_bytes() -> int | None:
    """Peak RSS via POSIX getrusage, or PeakWorkingSetSize on Windows."""
    if sys.platform != "win32":
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1 if platform.system() == "Darwin" else 1024))
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    get_handle = windll.kernel32.GetCurrentProcess
    get_handle.restype = ctypes.c_void_p
    info = windll.psapi.GetProcessMemoryInfo
    info.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryCounters), ctypes.c_ulong]
    info.restype = ctypes.c_int
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(MemoryCounters)
    ok = info(get_handle(), ctypes.byref(counters), counters.cb)
    return int(counters.PeakWorkingSetSize) if ok else None


def _pool_checkout_latency(repo: JobRepository) -> float:
    started = time.perf_counter()
    with repo._engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    return round(time.perf_counter() - started, 6)


def _config_digest_from_committed_files(config_dir: Path, destination: Path) -> str:
    """Digest only the committed synthetic config; never read private exceptions.yaml."""
    from src.masking.config import load_config

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_dir / "labels.yaml", destination / "labels.yaml")
    shutil.copy2(config_dir / "exceptions.example.yaml", destination / "exceptions.example.yaml")
    shutil.copytree(config_dir / "lexicons", destination / "lexicons", dirs_exist_ok=True)
    return load_config(destination).digest
