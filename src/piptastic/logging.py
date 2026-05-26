"""Logger factory. No module-level side effects."""

import logging
import sys
from pathlib import Path

_CONFIGURED = False


def get_logger(name: str = "piptastic") -> logging.Logger:
    """Return the piptastic logger. Idempotent."""
    return logging.getLogger(name)


def configure_logging(
    level: int = logging.WARNING,
    log_file: Path | None = None,
) -> None:
    """Configure the piptastic logger. Call once from the CLI entry point."""
    global _CONFIGURED
    logger = logging.getLogger("piptastic")
    logger.setLevel(level)

    if _CONFIGURED:
        # Reconfigure: tear down old handlers first so repeated calls in tests
        # don't accumulate.
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    logger.addHandler(stderr)

    if log_file is not None:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    logger.propagate = False
    _CONFIGURED = True
