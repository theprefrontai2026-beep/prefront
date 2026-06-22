"""Timestamped debug logging for the semantic layer.

Every module gets a logger via ``get_logger(__name__)``. Output is line-oriented
with millisecond timestamps so a whole design-time run (catalog build → dbt
import → bindings → query templates → validation) can be traced step by step on
stderr. The level is controlled by ``SEMANTICLAYER_LOG_LEVEL`` (default
``DEBUG``); set it to ``INFO``/``WARNING`` to quiet the trace in production.

The module that wires this in is intentionally tiny and side-effect free until
``get_logger`` is first called, so importing it never reconfigures the root
logger out from under a host process (e.g. uvicorn).
"""

from __future__ import annotations

import logging
import os
import sys

# Millisecond timestamps + level + module so each line is greppable on its own.
_FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_ROOT = "semanticlayer"
_configured = False


def _configure() -> None:
    """Attach a single stderr handler to the ``semanticlayer`` logger once."""
    global _configured
    if _configured:
        return
    level_name = os.environ.get("SEMANTICLAYER_LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))

    root = logging.getLogger(_ROOT)
    root.handlers[:] = [handler]      # own our handler; don't stack duplicates
    root.setLevel(level)
    root.propagate = False            # don't double-log through the global root
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a timestamped child logger, e.g. ``get_logger(__name__)``.

    The returned logger is namespaced under ``semanticlayer.<module>`` so every
    line is attributed to the stage that emitted it.
    """
    _configure()
    short = name.rsplit(".", 1)[-1]
    return logging.getLogger(f"{_ROOT}.{short}")
