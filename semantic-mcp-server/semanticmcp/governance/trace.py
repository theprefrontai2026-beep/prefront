"""Trace stage — durable decision traces (the audit record).

Every governed call appends one JSON line to TRACE_PATH (default
/data/traces.jsonl) and the same trace is returned in the tool response. The
trace is the product: what was asked, by whom, which rules fired, what was
decided, what executed.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .context import Caller, Decision


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
                "decision": o.decision,
                "fired": o.fired,
                **({"missing": o.missing} if o.missing else {}),
            }
            for o in decision.outcomes
        ],
        "execution_status": execution_status,
    }


def persist(trace: dict) -> None:
    """Append to the trace log; never let auditing break the call itself."""
    path = os.environ.get("TRACE_PATH", "/data/traces.jsonl")
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trace, default=str) + "\n")
    except OSError:
        pass
