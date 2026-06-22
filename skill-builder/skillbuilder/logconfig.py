"""Debug logging setup for manual runs.

Library modules log through ``logging.getLogger(__name__)`` and stay silent until
something configures the ``skillbuilder`` logger. Two front doors turn it on:

* CLI: ``-v`` (INFO) / ``-vv`` (DEBUG) on ``python -m skillbuilder build``.
* Service / env: ``SKILLBUILDER_LOG_LEVEL=DEBUG`` (honored by the FastAPI app
  too, so a manual run through the UI shows the same stage-by-stage trace).

Output goes to stderr so it never pollutes the artifacts/JSON the CLI prints to
stdout. Idempotent: calling it twice will not double-attach handlers.
"""

from __future__ import annotations

import logging
import os

_PKG = "skillbuilder"
_CONFIGURED = False

# -v -> INFO, -vv -> DEBUG; 0 leaves the package at WARNING (effectively quiet).
_VERBOSITY = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}


def _resolve_level(verbosity: int | None) -> int:
    if verbosity:
        return _VERBOSITY.get(verbosity, logging.DEBUG)
    env = os.environ.get("SKILLBUILDER_LOG_LEVEL")
    if env:
        return logging.getLevelName(env.strip().upper())  # name -> int
    return logging.WARNING


def setup_logging(verbosity: int | None = None) -> int:
    """Configure the ``skillbuilder`` logger and return the effective level.

    ``verbosity`` is the CLI ``-v`` count; when falsy, ``SKILLBUILDER_LOG_LEVEL``
    decides, defaulting to WARNING (no debug noise).
    """
    global _CONFIGURED
    level = _resolve_level(verbosity)
    logger = logging.getLogger(_PKG)
    logger.setLevel(level)
    if not _CONFIGURED:
        handler = logging.StreamHandler()  # stderr
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.propagate = False  # don't double-print under uvicorn's root logger
        _CONFIGURED = True
    return level
