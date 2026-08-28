"""Session-state accumulation for Family 2 inline reuse (autonomous_build.md
step 18, continued past the first pass - see eval-engine/CLAUDE.md's
"Phase D / step 18" section for the investigation that found this was the
missing prerequisite): a bounded, per-connection, in-memory history of
completed Steps, keyed by a session id set once per SSE connection (a
sibling to governance/identity.py's `act_as_var` - same lifetime, same
"set once at connect, read on every call over that connection" pattern).

This is what lets Family 2's PARAMETER-SIDE checks (param_provenance,
param_mutation, param_discard, param_taint, param_staleness,
entity_consistency - the ones autonomous_build.md step 18 names) see
earlier calls in the same connection, not just the current one.
Result-side checks (result_fidelity, error_blindness, approval_evidence,
minimization) still aren't wired here - they need a final answer, which a
single governed MCP tool call never produces (there's no "turn" here, only
tool calls); that's unchanged from the first pass.

In-memory only, per-process, bounded (a session's own history is capped;
old sessions are evicted LRU-style) - this is a POC-quality runtime already
(policy/templates hot-reload from a file, traces go to a JSONL file, no
database-backed state anywhere in this service) not a durability guarantee.
A process restart clears all history, same as it clears the in-memory
identity cache in identity.py.
"""

from __future__ import annotations

import contextvars
import uuid
from collections import OrderedDict
from typing import Optional

from ..evalengine.contract import Step

# Per-CONNECTION session id, set once when the SSE connection opens (see
# server.py's handle_sse) and read on every call made over that connection -
# same lifetime/pattern as identity.py's act_as_var, deliberately not reused
# for IT (two different connections authenticating as the same caller must
# never share tool-call history).
session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "prefront_governance_session_id", default=None)

MAX_SESSIONS = 500
MAX_STEPS_PER_SESSION = 200

_sessions: "OrderedDict[str, list[Step]]" = OrderedDict()


def start_session() -> str:
    """Call once per new connection; returns the id to set on session_id_var."""
    sid = uuid.uuid4().hex
    _sessions[sid] = []
    _evict()
    return sid


def end_session(session_id: Optional[str]) -> None:
    """Call once per connection close, to free memory promptly rather than
    waiting for LRU eviction."""
    if session_id:
        _sessions.pop(session_id, None)


def steps_for(session_id: Optional[str]) -> tuple[Step, ...]:
    if not session_id:
        return ()
    return tuple(_sessions.get(session_id, ()))


def append_step(session_id: Optional[str], step: Step) -> None:
    if not session_id:
        return
    history = _sessions.setdefault(session_id, [])
    history.append(step)
    if len(history) > MAX_STEPS_PER_SESSION:
        del history[0]
    _sessions.move_to_end(session_id)
    _evict()


def _evict() -> None:
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)


def session_count() -> int:
    """Test/ops hook."""
    return len(_sessions)
