"""
Conductor for the isolated operational drill suite.

The drill logic lives in ``back/tests/drill_support.py``; this CLI only applies
migrations to a caller-supplied *disposable* PostgreSQL database, runs the
dedicated pytest module and persists its JSON evidence. It refuses URLs whose
database name does not look disposable, so it cannot be pointed at a live stand.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]


def _is_disposable_database(url: str) -> bool:
    """Accept only database names explicitly reserved for drills/tests/CI."""
    name = (make_url(url).database or "").lower()
    return "drill" in name or name.startswith("test") or name.endswith("_ci")


def redact_database_url(url: str) -> str:
    """Render a connection target without any credentials or secrets."""
    parsed = make_url(url)
    return f"postgresql://<redacted>@{parsed.host}:{parsed.port}/{parsed.database}"


def _scrub(output: str, url: str) -> str:
    secret = make_url(url).password
    if secret:
        output = output.replace(secret, "<redacted>")
    return output.replace(url, redact_database_url(url))


def _run(command: list[str], *, env: dict[str, str], url: str) -> int:
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, check=False)  # noqa: S603 - fixed CLI
    if result.stdout:
        sys.stdout.write(_scrub(result.stdout.decode("utf-8", errors="replace"), url))
    if result.stderr:
        sys.stdout.write(_scrub(result.stderr.decode("utf-8", errors="replace"), url))
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    """Entry point: migrate, run drills, write the evidence file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, help="URL of a disposable drill database")
    parser.add_argument(
        "--report",
        default=str(ROOT / "back" / "benchmarks" / "operational-drill-results.json"),
        help="Path for the JSON evidence file",
    )
    parser.add_argument("--pg-container", default="", help="Optional dm-drill-* container for backup rehearsal")
    args = parser.parse_args(argv)
    if not _is_disposable_database(args.database_url):
        parser.error("refusing a non-disposable database name (need 'drill', 'test*' or '*_ci')")

    report = Path(args.report)
    if not report.is_absolute():
        report = ROOT / report
    report = report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DATABASE_URL"] = args.database_url
    env["TEST_DATABASE_URL"] = args.database_url
    env["DOCMASKER_DRILL_REPORT"] = str(report)
    env["DOCMASKER_DRILL_PG_CONTAINER"] = args.pg_container
    with tempfile.TemporaryDirectory(prefix="dm-drill-jobs-") as spool:
        env["JOBS_DIR"] = spool
        code = _run(
            ["uv", "run", "--directory", "back", "alembic", "upgrade", "head"],
            env=env,
            url=args.database_url,
        )
        if code != 0:
            return code
        code = _run(
            [
                "uv",
                "run",
                "--directory",
                "back",
                "pytest",
                "-q",
                "tests/test_operational_drills.py",
                "-s",
            ],
            env=env,
            url=args.database_url,
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
