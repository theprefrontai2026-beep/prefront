"""Environment-driven settings. Every knob has a compose-friendly default."""

from __future__ import annotations

import os

# 0.2.0: content.evaluate emits final_answer-scoped verdicts per TURN rather
# than per step (one answer-scoped leak was reported once per tool call), and
# result_fidelity scans standalone number tokens rather than every digit run
# (hyphenated and masked identifiers read as fabricated numeric claims).
# 0.2.1: result_fidelity also grounds a claim on a number the USER supplied -
# echoing back "a 50 basis point discount" was reported as a fabrication.
# Each bump changes verdict output, and forces already-evaluated sessions back
# through evaluation via the version key.
ENGINE_VERSION = "0.2.1"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


CLICKHOUSE_URL = _env("CLICKHOUSE_URL", "http://clickhouse:8123")
CLICKHOUSE_USER = _env("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = _env("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DB = _env("CLICKHOUSE_DB", "prefront")

EVAL_HOST = _env("EVAL_HOST", "0.0.0.0")
EVAL_PORT = int(_env("EVAL_PORT", "8120"))
EVAL_LOG_LEVEL = _env("EVAL_LOG_LEVEL", "INFO")

# How often the worker looks for newly-closed sessions to evaluate.
EVAL_POLL_SECONDS = float(_env("EVAL_POLL_SECONDS", "10"))
# A session with no new spans for this long is considered closed. Sessions
# still receiving spans are left alone - re-running mid-session would emit
# verdicts against a partial trace that get contradicted a moment later.
EVAL_QUIET_SECONDS = float(_env("EVAL_QUIET_SECONDS", "10"))

# Path to the trace_binding.yaml this deployment uses. Empty = the bundled
# default profile (matches prefront_tracing.py's own conventions).
TRACE_BINDING_PATH = _env("EVAL_TRACE_BINDING_PATH", "")
VISIBILITY_PROFILE_PATH = _env("EVAL_VISIBILITY_PROFILE_PATH", "")

# Family 1 (customer rule pack) and Family 3 (intent catalog): empty path =
# not configured, Family 1/3 evaluate to zero verdicts (Hard Rule 9), never
# an error. Published by skill-builder / semantic-layer respectively.
RULE_PACK_PATH = _env("EVAL_RULE_PACK_PATH", "")
INTENT_CATALOG_PATH = _env("EVAL_INTENT_CATALOG_PATH", "")

# Deployment mode for the standalone worker/API. Phase A only ever runs OOB;
# "inline" mode of the *combinator* is exercised by semantic-mcp-server
# importing evalengine directly (Phase D), not by this service.
EVAL_MODE = _env("EVAL_MODE", "oob")

# Tolerances for the provenance whitelist (see provenance.py). Numeric
# rounding/derivation beyond these is param_mutation, not satisfied.
PARAM_ROUND_ABS_TOLERANCE = float(_env("EVAL_ROUND_ABS_TOLERANCE", "0.01"))
PARAM_ROUND_REL_TOLERANCE = float(_env("EVAL_ROUND_REL_TOLERANCE", "0.005"))

# minimization: a call fetching more than this many rows, or more than this
# multiple of the session's median row_count for the same tool, is flagged.
MINIMIZATION_ROW_FLOOR = int(_env("EVAL_MINIMIZATION_ROW_FLOOR", "50"))
MINIMIZATION_MULTIPLE = float(_env("EVAL_MINIMIZATION_MULTIPLE", "5.0"))
