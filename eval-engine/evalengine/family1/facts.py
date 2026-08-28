"""Per-step fact bag: args + result leaves + caller.* + entity-correlated
facts from earlier steps in the SAME session - what predicate/temporal
conditions are evaluated against. Mirrors semantic-mcp-server's governance
facts.py value namespace (precheck-row columns ∪ request args ∪ caller.<attr>)
as closely as an OOB trace allows: there is no precheck row here, only what
the trace itself reveals.
"""

from __future__ import annotations

from typing import Any

from ..contract import Session, Step
from ..provenance import flatten


def _id_pairs(value: Any) -> set[tuple[str, Any]]:
    """(key_suffix, value) for every "*_id"-named leaf - the same structural
    convention family2.entity_consistency uses to spot an identifier slot,
    reused here to correlate which steps share a subject."""
    out: set[tuple[str, Any]] = set()
    for path, v in flatten(value):
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf.endswith("_id") and not isinstance(v, bool) and v not in (None, ""):
            out.add((leaf, v))
    return out


def _entity_correlated_steps(step: Step, prior: list[Step]) -> list[Step]:
    """Every earlier step transitively linked to `step` by a shared (id_key,
    value) pair - so a later call can see a fact two hops back (e.g. a write
    that only carries loan_id still reaches the credit score an earlier
    get_credit_report(applicant_id=...) returned, bridged by a get_application
    call whose result names both ids). Matching is scoped to the SAME id key
    name, never "any id equals any other id" - a loan_id can only match
    another loan_id, so numerically colliding but unrelated ids never merge.
    """
    ids_by_step = {s.seq: _id_pairs(s.args) | _id_pairs(s.result) for s in prior}
    own_ids = _id_pairs(step.args) | _id_pairs(step.result)
    reachable: set[int] = set()
    frontier = {s.seq for s in prior if ids_by_step[s.seq] & own_ids}
    while frontier:
        reachable |= frontier
        acc_ids: set[tuple[str, Any]] = set()
        for seq in frontier:
            acc_ids |= ids_by_step[seq]
        frontier = {s.seq for s in prior if s.seq not in reachable and ids_by_step[s.seq] & acc_ids}
    return [s for s in prior if s.seq in reachable]


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

    prior = [s for s in session.steps if s.seq < step.seq]
    for s in _entity_correlated_steps(step, prior):
        for path, v in flatten(s.result):
            leaf = path.rsplit(".", 1)[-1].split("[")[0]
            facts.setdefault(leaf, v)  # this step's own facts always win
    return facts
