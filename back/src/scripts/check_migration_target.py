"""
Preflight for the one-off Docker Alembic migration.

Prints the target host/port/database/schema (never a password) and refuses an
ambiguous local target unless ``ALLOW_LOCAL_DB=1`` confirms it. Also requires
an explicit, non-``latest`` image pin so the migration cannot silently run
from a stale cached image.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO
from urllib.parse import parse_qsl, urlsplit

DEFAULT_PORT = 5432
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres", "db", "dm-postgres", "host.docker.internal"})
SEARCH_PATH_RE = re.compile(r"(?:search_path|currentSchema)\s*=\s*([^\s,;]+)", re.IGNORECASE)


@dataclass(frozen=True)
class MigrationTarget:
    """The database the one-off container will actually reach."""

    host: str
    port: int
    database: str
    schema: str
    source: str

    @property
    def is_local(self) -> bool:
        """True for compose/loopback targets, where a wrong-DB mistake is likely."""
        host = self.host.lower()
        return host in LOCAL_HOSTS or host.startswith("127.")


def _schema_from_query(query: str) -> str:
    """Extract search_path from psycopg `options`, or an explicit `schema`."""
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key.lower() == "schema":
            candidate = value.strip("\"'")
            if candidate:
                return candidate
        if key.lower() == "options":
            match = SEARCH_PATH_RE.search(value)
            if match:
                candidate = match.group(1).strip("\"'")
                if candidate:
                    return candidate
    return "public"


def target_from_url(url: str) -> MigrationTarget:
    """Parse an explicit DATABASE_URL without ever echoing its credentials."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"postgresql", "postgresql+psycopg"}:
        msg = "DATABASE_URL must be a PostgreSQL URL"
        raise ValueError(msg)
    if not parts.hostname:
        msg = "DATABASE_URL must include a host"
        raise ValueError(msg)
    try:
        port = parts.port or DEFAULT_PORT
    except ValueError as exc:
        msg = "DATABASE_URL has an invalid port"
        raise ValueError(msg) from exc
    database = parts.path.lstrip("/").split("/", 1)[0]
    if not database:
        msg = "DATABASE_URL must include a database name"
        raise ValueError(msg)
    return MigrationTarget(parts.hostname, port, database, _schema_from_query(parts.query), "DATABASE_URL (-e)")


def target_from_compose(env: Mapping[str, str]) -> MigrationTarget:
    """Mirror the compose `x-db-env` URL built from POSTGRES_* components."""
    database = env.get("POSTGRES_DB", "").strip()
    if not database:
        msg = "POSTGRES_DB is required to resolve the compose database target"
        raise ValueError(msg)
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD"):
        if not env.get(name):
            msg = f"{name} is required by the compose x-db-env anchor"
            raise ValueError(msg)
    return MigrationTarget(
        env.get("POSTGRES_HOST_DOCKER") or "postgres",
        DEFAULT_PORT,
        database,
        "public",
        "POSTGRES_HOST_DOCKER (compose x-db-env)",
    )


def resolve_target(env: Mapping[str, str]) -> MigrationTarget:
    """Resolve the target: an explicit URL wins, as in env.py and `compose run -e`."""
    explicit = env.get("DATABASE_URL")
    return target_from_url(explicit) if explicit else target_from_compose(env)


def validate_image_pin(env: Mapping[str, str]) -> str:
    """Require the deployed release tag or digest; reject `:latest`/untagged."""
    image = env.get("DM_BACK_IMAGE", "").strip()
    if not image:
        msg = "DM_BACK_IMAGE must pin the exact deployed image tag or digest"
        raise ValueError(msg)
    name = image.rsplit("/", 1)[-1]
    if "@sha256:" in name:
        return image
    if ":" not in name:
        msg = "DM_BACK_IMAGE needs an explicit tag or digest, never an untagged image"
        raise ValueError(msg)
    tag = name.rsplit(":", 1)[1]
    if not tag or tag.lower() == "latest":
        msg = "DM_BACK_IMAGE must never resolve to `:latest` (the cached image may be stale)"
        raise ValueError(msg)
    return image


def render(env: Mapping[str, str], stdout: TextIO, stderr: TextIO) -> int:
    """Print the target and return 0 only when the migration target is safe."""
    try:
        target = resolve_target(env)
    except ValueError as exc:
        stderr.write(f"ERROR: {exc}\n")
        return 1

    errors: list[str] = []
    try:
        image = validate_image_pin(env)
    except ValueError as exc:
        image = "<invalid>"
        errors.append(str(exc))

    stdout.write("Docker migration preflight (credentials are never printed)\n")
    stdout.write(f"  target host     : {target.host}\n")
    stdout.write(f"  target port     : {target.port}\n")
    stdout.write(f"  target database : {target.database}\n")
    stdout.write(f"  target schema   : {target.schema}\n")
    stdout.write(f"  target source   : {target.source}\n")
    stdout.write(f"  image           : {image}\n")
    if target.is_local and env.get("ALLOW_LOCAL_DB") != "1":
        errors.append(
            "Ambiguous LOCAL database target; confirm the disposable/local stand with ALLOW_LOCAL_DB=1 "
            "or point POSTGRES_HOST_DOCKER / DATABASE_URL at the intended external server"
        )
    if errors:
        stderr.writelines(f"ERROR: {error}\n" for error in errors)
        stderr.write("Refusing to run the migration.\n")
        return 1
    stdout.write("Preflight OK: target confirmed; run exactly one migration container.\n")
    return 0


def main(env: Mapping[str, str] | None = None) -> int:
    """CLI entry point; uses the process environment by default."""
    return render(os.environ if env is None else env, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
