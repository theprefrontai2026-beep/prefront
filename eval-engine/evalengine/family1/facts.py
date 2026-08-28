"""Per-step fact bag: args + result leaves + caller.* - what predicate/temporal
conditions are evaluated against. Mirrors semantic-mcp-server's governance
facts.py value namespace (precheck-row columns ∪ request args ∪ caller.<attr>)
as closely as an OOB trace allows: there is no precheck row here, only what
the call's own args/result reveal.
"""

from __future__ import annotations

from typing import Any

from ..contract import Session, Step
from ..provenance import flatten


def build_facts(step: Step, session: Session) -> dict[str, Any]:
    facts: dict[str, Any] = {}

    def add(path: str, value: Any) -> None:
        facts[path] = value
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        facts.setdefault(leaf, value)

    for path, v in flatten(step.args):
        add(path, v)
    for path, v in flatten(step.result, "result"):
        add(path, v)
    facts["caller.role"] = session.caller_role
    facts["caller.channel"] = session.channel
    facts["caller.user_id"] = session.user_id
    facts["intent"] = step.intent or step.tool_name
    return facts
