"""Seed DocMasker's PostgreSQL substrate (mirrors ReportCollector's seed_database)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from src.config import Settings, get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("docmasker.seed")


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _database_url(settings: Settings, host: str | None) -> str:
    """Optionally replace the host from DATABASE_URL (host-side seed vs compose)."""
    if host is None:
        return settings.database_url
    url = make_url(settings.database_url)
    target_host, _, target_port = host.partition(":")
    return url.set(host=target_host, port=int(target_port) if target_port else None).render_as_string(
        hide_password=False
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        help="Override DATABASE_URL host (e.g. 127.0.0.1:15433 when seeding from the host)",
    )
    parser.add_argument("--reset", action="store_true", help="Drop the jobs table before migrating")
    args = parser.parse_args()

    url = _database_url(get_settings(), args.host)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        schema = inspect(connection).get_schema_names()
        if "public" not in schema:
            connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    if args.reset:
        command.downgrade(_alembic_config(url), "base")
    command.upgrade(_alembic_config(url), "head")

    engine = create_engine(url)
    with engine.connect() as connection:
        tables = inspect(connection).get_table_names()
    engine.dispose()
    if "jobs" not in tables:
        msg = "Misleading seed: the jobs table is still missing."
        raise SystemExit(msg)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    LOGGER.info("PostgreSQL seeded: jobs table present at %s", url.split("@")[-1])


if __name__ == "__main__":
    main()
