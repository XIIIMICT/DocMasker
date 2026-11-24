"""
Validate the masking configuration without starting the web application.

Usage (from `back/`):
    uv run python -m src.masking.validate_config
    uv run python -m src.masking.validate_config --dir ./config/masking
"""

import argparse
import logging
import sys

from .config import MaskingConfigError, load_config

LOGGER = logging.getLogger("docmasker.config")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the DocMasker masking configuration.")
    parser.add_argument("--dir", dest="directory", default=None, help="Path to config/masking (default: settings).")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.directory)
    except MaskingConfigError as error:
        LOGGER.error("Некорректная конфигурация маскирования: %s", error)  # noqa: TRY400 (CLI: no traceback)
        return 1
    # Safe metadata only: never log exception values.
    LOGGER.info(
        "Конфигурация маскирования валидна: labels=%d, schema=%d, digest=%s",
        len(config.labels.entries),
        1,
        config.digest[:12],
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
