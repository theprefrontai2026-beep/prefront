"""Identity stage — resolve the trusted caller context. Pure mechanism.

HOW a caller maps to attributes is deployment CONFIGURATION, not Prefront code:

  IDENTITY_QUERY  SQL with a ``:who`` bind that returns one row of caller
                  attributes. The deployment aliases its own schema onto the
                  contract names (``role``, ``region``) plus anything else its
                  write specs need, e.g.:
                    SELECT rep_id, name, region_id AS region, role
                    FROM sales_reps WHERE email = :who
  ACT_AS          the value bound to :who (the identity the server runs as)
  CALLER_ROLE /   explicit fallback attributes when no lookup query is
  CALLER_REGION   configured (no database involved)

The agent can never pass or spoof ``caller_*`` values — they are injected from
here. Future module: per-session authN replaces this stage with the same
return type.
"""

from __future__ import annotations

import contextvars
import os
from typing import Optional

from .. import db
from .context import Caller

_cache: dict[tuple, Optional[Caller]] = {}

# Per-CONNECTION caller identity. The HTTP/SSE transport sets this from the
# trusted session at connect time (see serve_http), so ONE server process can
# serve many callers — each connection resolves its own identity. It always wins
# over the process-wide ACT_AS env (which remains the single-identity default for
# stdio / a per-process deployment). The agent never sets this; the session does.
act_as_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "prefront_act_as", default=None)


def configured() -> bool:
    return bool(
        (os.environ.get("ACT_AS") and os.environ.get("IDENTITY_QUERY"))
        or os.environ.get("CALLER_ROLE")
        or (act_as_var.get() and os.environ.get("IDENTITY_QUERY"))
    )


def resolve_caller(dsn: str) -> Optional[Caller]:
    """Resolve (and cache) the caller. Per-connection identity (act_as_var) wins
    over the process-wide ACT_AS env; None when no identity is set."""
    act_as = (act_as_var.get() or os.environ.get("ACT_AS", "")).strip()
    query = os.environ.get("IDENTITY_QUERY", "").strip()
    role = os.environ.get("CALLER_ROLE", "").strip()
    region = os.environ.get("CALLER_REGION", "").strip()
    key = (dsn, act_as, query, role, region)
    if key in _cache:
        return _cache[key]

    caller: Optional[Caller] = None
    if act_as and query:
        rows = db.run_select(dsn, query, {"who": act_as})
        if rows:
            caller = Caller(attrs=dict(rows[0]), source=f"act_as:{act_as}")
    elif role:
        attrs = {"role": role}
        if region:
            attrs["region"] = region
        caller = Caller(attrs=attrs, source="explicit")

    _cache[key] = caller
    return caller
