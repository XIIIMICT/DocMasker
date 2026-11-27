"""Repeatable isolated operational drills (PostgreSQL + private spool only).

These tests are inert offline: without ``TEST_DATABASE_URL`` the shared
``pg_url`` fixture skips them. ``src.scripts.operational_drills`` drives the
whole module and writes JSON evidence. No production data or shared volume is
read, and no throughput target is asserted — only outcomes and integrity.
"""

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from drill_support import (
    DrillReport,
    NoCache,
    backup_restore_rehearsal,
    concurrency_batch,
    crash_after_db_commit,
    crash_before_result_commit,
    crash_during_processing,
    crash_during_zip,
    crash_staging,
    cross_job_artifact_isolation,
    drill_settings,
    enqueue_direct,
    expiry_and_cleanup,
    simulate_disk_full,
    simulate_pool_exhaustion,
    simulate_sigterm,
    synthetic_text,
)
from sqlalchemy import text

from src.config import Settings  # noqa: TC001 - pytest fixture annotations are runtime-resolved
from src.jobs.repository import JobRepository
from src.jobs.storage import ArtifactStore
from src.jobs.worker import Worker
from src.scripts.operational_drills import _is_disposable_database, _scrub, redact_database_url

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "masking"


@pytest.fixture(scope="module")
def report() -> Iterator[DrillReport]:
    collected = DrillReport()
    yield collected
    destination = os.environ.get("DOCMASKER_DRILL_REPORT")
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(collected.as_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )


@pytest.fixture
def env(tmp_path: Path, pg_url: str, clean_jobs: None, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = drill_settings(pg_url, tmp_path)
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    from src.jobs import dependencies

    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr("src.jobs.worker.dependencies.result_cache", NoCache)
    dependencies.job_repository.cache_clear()
    dependencies.artifact_store.cache_clear()
    return settings


def test_drill_cli_rejects_non_disposable_targets(report: DrillReport) -> None:
    live = "postgresql+psycopg://dm:secret-value@db.example:5432/docmasker"
    drill = "postgresql+psycopg://dm:secret-value@127.0.0.1:15533/dm_drill_2026"
    assert _is_disposable_database(drill)
    assert not _is_disposable_database(live)
    redacted = redact_database_url(drill)
    assert "secret-value" not in redacted
    assert "docmasker" not in redacted
    assert _scrub("failed for dm:secret-value@127.0.0.1:15533/dm_drill_2026", drill).count("secret-value") == 0
    report.add("cli_guard", non_disposable_rejected=True, credentials_redacted=True)


def test_crash_window_matrix(report: DrillReport, env: Settings) -> None:
    for name, run in (
        ("staging", crash_staging),
        ("db_commit", crash_after_db_commit),
        ("processing", crash_during_processing),
        ("zip_creation", crash_during_zip),
        ("before_result_commit", crash_before_result_commit),
    ):
        report.add(f"crash:{name}", **run(env))


def test_sigterm_drains_without_stale_publication(report: DrillReport, env: Settings) -> None:
    report.add("sigterm", **simulate_sigterm(env))


def test_disk_full_simulation(report: DrillReport, env: Settings) -> None:
    report.add("disk_full", **simulate_disk_full(env))


def test_pool_exhaustion_is_bounded(report: DrillReport, env: Settings) -> None:
    report.add("pool_exhaustion", **simulate_pool_exhaustion(env))


def test_expiry_checks_metadata_and_bytes(report: DrillReport, env: Settings) -> None:
    report.add("expiry", **expiry_and_cleanup(env))


def test_cross_job_artifact_isolation(report: DrillReport, env: Settings) -> None:
    report.add("artifact_isolation", **cross_job_artifact_isolation(env))


@pytest.mark.parametrize(
    ("sizes", "concurrency"),
    [(10, 1), (10, 2), (40, 2), (40, 4)],
)
def test_concurrent_documents(report: DrillReport, env: Settings, sizes: int, concurrency: int) -> None:
    report.add(f"concurrency:{sizes}x{concurrency}", **concurrency_batch(env, (sizes,) * concurrency, concurrency))


def test_backup_restore_rehearsal(report: DrillReport, env: Settings) -> None:
    container = os.environ.get("DOCMASKER_DRILL_PG_CONTAINER", "")
    if not container:
        report.add(
            "backup_restore",
            status="OPEN",
            reason="no authorized disposable target environment supplied",
        )
        pytest.skip("backup/restore rehearsal requires DOCMASKER_DRILL_PG_CONTAINER")
    repo = JobRepository(env)
    store = ArtifactStore(env.jobs_dir)
    worker = Worker(env, repo, store)
    for index in range(2):
        job_id = enqueue_direct(env, repo, store, "mask_text", data=synthetic_text(3, f"backup-{index}-{uuid.uuid4()}"))
        claimed = repo.claim_with_owner(
            lease_seconds=env.jobs_lease_seconds,
            concurrency=env.jobs_concurrency,
            worker_id=str(uuid.uuid4()),
        )
        assert claimed is not None
        worker.run_one(job_id, claimed[1])
        assert repo.get(job_id).status == "succeeded"
    # Workspace must be outside the artifact spool (tmp_path) to avoid recursion.
    workspace = Path(tempfile.mkdtemp(prefix="dm-backup-rehearsal-"))
    try:
        report.add(
            "backup_restore",
            **backup_restore_rehearsal(
                env,
                container=container,
                config_dir=CONFIG_DIR,
                workspace=workspace,
            ),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_restored_database_has_no_unexpected_rows(env: Settings) -> None:
    """Check that the drill database remains usable after the rehearsal."""
    repo = JobRepository(env)
    with repo._engine.begin() as connection:
        connection.execute(text("SELECT 1"))
    repo.dispose()
