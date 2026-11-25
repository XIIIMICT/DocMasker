"""Seed DocMasker's PostgreSQL substrate (mirrors ReportCollector's seed_database)."""

import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from src.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("docmasker.seed")


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def main() -> None:
    """
    Apply Alembic migrations using the host-side POSTGRES_* settings.

    No ``--host`` override is needed: ``Settings.sqlalchemy_database_url``
    builds the URL from POSTGRES_HOST / POSTGRES_PORT in the root .env (host
    values), while compose overrides DATABASE_URL with POSTGRES_HOST_DOCKER
    for containers — the ReportCollector pattern.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Drop the jobs table before migrating")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    url = get_settings().sqlalchemy_database_url
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
    LOGGER.info("PostgreSQL seeded: jobs table present at %s", url.split("@")[-1])


if __name__ == "__main__":
    main()
