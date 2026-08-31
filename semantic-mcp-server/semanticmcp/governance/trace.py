"""Trace stage — durable decision traces (the audit record).

Every governed call appends one JSON line to TRACE_PATH (default
/data/traces.jsonl) and the same trace is returned in the tool response. The
trace is the product: what was asked, by whom, which rules fired, what was
decided, what executed.

Two integrity properties, both added for compliance_design.md §5.4:

* **Hash chain.** Each line carries `prev_hash` (the previous line's `hash`,
  or a genesis constant for the first line) and `hash` = SHA-256 over
  `prev_hash` + the canonical JSON of the record without those two keys. A
  line edited, dropped or inserted after the fact breaks every subsequent
  link; `verify_chain()` walks a file and reports the first bad line. The
  chain is per file: on startup (or when TRACE_PATH changes) the last line's
  hash is read back so a restart continues the chain rather than forking it.
  The hash rides in the tool response too (`governance.hash`), so a client's
  copy of a decision can be matched to the log line that recorded it.
* **Failures are counted, not swallowed.** `persist` still never raises -
  auditing must not break the call - but it returns False and increments a
  counter the server surfaces on /healthz (`audit.failures`,
  `audit.last_error`). A full disk used to be invisible; now it is a number
  that is wrong when it is not zero.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .context import Caller, Decision

GENESIS_HASH = "0" * 64
_CHAIN_KEYS = ("prev_hash", "hash")

_lock = threading.Lock()
_state: dict[str, Any] = {"path": None, "last_hash": None, "written": 0, "failures": 0, "last_error": ""}


def build_trace(
    *,
    intent: str,
    tool: str,
    caller: Optional[Caller],
    args: dict[str, Any],
    decision: Decision,
    execution_status: str,
    template_id: Optional[str] = None,
) -> dict:
    return {
        "trace_id": "trace_" + uuid.uuid4().hex[:12],
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool,
        "matched_intent": intent,
        "template_id": template_id,
        "caller": caller.as_dict() if caller else None,
        "parameters": args,
        "decision": decision.status,
        "reasons": decision.reasons,
        "approver_roles": decision.approver_roles,
        "masked_fields": decision.mask_fields,
        "rules_evaluated": [
            {
                "rule_key": o.rule_key,
                "rule_type": o.rule_type,
                "decision": o.decision,
                "fired": o.fired,
                "indeterminate": o.indeterminate,
                "conditions": o.conditions,
                **({"source": o.source} if o.source else {}),
                **({"reason": o.reason} if o.reason else {}),
                **({"restricted_fields": o.restricted_fields} if o.restricted_fields else {}),
                **({"approver_role": o.approver_role} if o.approver_role else {}),
                **({"missing": o.missing} if o.missing else {}),
            }
            for o in decision.outcomes
        ],
        "execution_status": execution_status,
    }


# --- hash chain ---------------------------------------------------------------

def _canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k not in _CHAIN_KEYS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def link_hash(prev_hash: str, record: dict) -> str:
    return hashlib.sha256(prev_hash.encode("ascii") + b"\n" + _canonical(record)).hexdigest()


def _tail_hash(path: Path) -> str:
    """The `hash` of the last non-empty line, GENESIS_HASH for a missing/empty
    file or a last line written before chaining existed (that line simply
    starts a chain)."""
    if not path.exists():
        return GENESIS_HASH
    last = ""
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
    if not last:
        return GENESIS_HASH
    try:
        h = json.loads(last).get("hash")
    except (ValueError, AttributeError):
        return GENESIS_HASH
    return h if isinstance(h, str) and len(h) == 64 else GENESIS_HASH


def trace_path() -> str:
    return os.environ.get("TRACE_PATH", "/data/traces.jsonl")


def persist(trace: dict) -> bool:
    """Append to the trace log with its chain link. Never raises - auditing
    must not break the call - but says whether it succeeded, and keeps the
    count (see audit_status)."""
    path = trace_path()
    with _lock:
        if _state["path"] != path:
            _state["path"] = path
            _state["last_hash"] = None
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if _state["last_hash"] is None:
                _state["last_hash"] = _tail_hash(p)
            prev = _state["last_hash"]
            trace["prev_hash"] = prev
            trace["hash"] = link_hash(prev, trace)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(trace, default=str) + "\n")
            _state["last_hash"] = trace["hash"]
            _state["written"] += 1
            return True
        except OSError as e:
            _state["failures"] += 1
            _state["last_error"] = f"{type(e).__name__}: {e}"
            # the record still goes back to the caller, unchained
            trace.pop("prev_hash", None)
            trace.pop("hash", None)
            return False


def audit_status() -> dict[str, Any]:
    with _lock:
        return {
            "path": _state["path"] or trace_path(),
            "written": _state["written"],
            "failures": _state["failures"],
            "last_error": _state["last_error"],
            "chained": True,
        }


def verify_chain(path: str | None = None) -> dict[str, Any]:
    """Walk a trace file and check every link. Returns
    {ok, lines, checked, first_bad_line, error}. A line without a hash
    (written before chaining) is reported as the first bad line only if a
    chained line follows it - a legacy prefix is tolerated, a gap is not."""
    p = Path(path or trace_path())
    result: dict[str, Any] = {"path": str(p), "ok": True, "lines": 0, "checked": 0, "first_bad_line": None, "error": ""}
    if not p.exists():
        result.update(ok=False, error="file not found")
        return result
    prev = GENESIS_HASH
    seen_chained = False
    with p.open("r", encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            if not line.strip():
                continue
            result["lines"] = n
            try:
                rec = json.loads(line)
            except ValueError:
                result.update(ok=False, first_bad_line=n, error="not JSON")
                return result
            if "hash" not in rec:
                if seen_chained:
                    result.update(ok=False, first_bad_line=n, error="unchained line after a chained one")
                    return result
                continue  # legacy prefix
            seen_chained = True
            if rec.get("prev_hash") != prev or link_hash(prev, rec) != rec.get("hash"):
                result.update(ok=False, first_bad_line=n, error="hash mismatch")
                return result
            prev = rec["hash"]
            result["checked"] += 1
    return result
