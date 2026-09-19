"""Run a scenario down both lanes and report what each one did.

The contract this file keeps is that the two lanes differ in exactly one
respect: whether a decision is asked for. Same calls, same order, same
execution path into the ledger, same data. When the columns diverge, the
enforcement plane is the only thing that could have caused it.

`prepare` deserves a word. Revocation is not something that happens before a
run — the whole point is that an operator presses stop while the agent is
working. So a scenario carrying a `prepare` hook has it fired AFTER the
governed lane's first settlement, mid-sequence, which is why `run_governed` is
called in two halves for those. The ungoverned lane gets no such hook, because
there is nothing to press stop on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import agent
import deployment
import scenarios
from world import Ledger, usd


@dataclass
class LaneResult:
    steps: list[agent.Step]
    ledger: Ledger

    @property
    def executed(self) -> int:
        return len([s for s in self.steps if s.outcome == "executed"])

    @property
    def blocked(self) -> int:
        return len([s for s in self.steps if s.outcome == "blocked"])

    @property
    def held(self) -> int:
        return len([s for s in self.steps if s.outcome == "held_for_approval"])

    @property
    def unauthorized_cents(self) -> int:
        """Money that moved and should not have.

        Counted from what EXECUTED, so it is a fact about the run rather than
        a restatement of the scenario's intent.
        """
        return sum(s.call.unauthorized_cents for s in self.steps if s.outcome == "executed")


@dataclass
class ScenarioResult:
    scenario: scenarios.Scenario
    ungoverned: LaneResult
    governed: LaneResult

    @property
    def prevented_cents(self) -> int:
        return self.ungoverned.unauthorized_cents - self.governed.unauthorized_cents

    @property
    def matched_expectation(self) -> bool:
        if not self.scenario.expect:
            return True
        return tuple(s.effect for s in self.governed.steps) == self.scenario.expect


def _split_point(scenario: scenarios.Scenario, calls: list) -> int:
    """Where a mid-run `prepare` fires: after the first call that moves money.

    Defined here rather than on the scenario because it is a property of how
    the demo tells the story ("the operator sees the first payment land, then
    presses stop"), not of the situation being demonstrated.
    """
    for i, call in enumerate(calls):
        if call.amount_cents:
            return i + 1
    return 1


def run(scenario: scenarios.Scenario) -> ScenarioResult:
    at = scenario.run_at if scenario.run_at is not None else deployment.NOW

    # Two independent deployments so neither lane's budget, call count or tree
    # state can leak into the other.
    dep_u = deployment.build(tree_id=f"{scenario.key.lower()}-ungoverned")
    dep_g = deployment.build(tree_id=f"{scenario.key.lower()}-governed")

    ledger_u, ledger_g = Ledger(), Ledger()

    calls_u = scenario.build(dep_u)
    calls_g = scenario.build(dep_g)

    ungoverned = LaneResult(agent.run_ungoverned(calls_u, ledger_u), ledger_u)

    if scenario.prepare is None:
        steps = agent.run_governed(
            calls_g, ledger_g, dep_g, now=at, subject=scenario.token_subject
        )
    else:
        cut = _split_point(scenario, calls_g)
        steps = agent.run_governed(
            calls_g[:cut], ledger_g, dep_g, now=at, subject=scenario.token_subject
        )
        scenario.prepare(dep_g)
        steps += agent.run_governed(
            calls_g[cut:], ledger_g, dep_g, now=at, subject=scenario.token_subject
        )

    return ScenarioResult(scenario, ungoverned, LaneResult(steps, ledger_g))


def run_all() -> list[ScenarioResult]:
    return [run(s) for s in scenarios.CATALOGUE]


# --- serialization for the console -----------------------------------------


def _excerpt(text: str, limit: int = 320) -> str:
    """Trim to a word boundary. A quotation cut mid-word reads as a rendering
    bug, and this text is being shown to make a point about what the agent
    read — it has to look like a document."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(" ")].rstrip(",.;:") + " …"


def _step_json(step: agent.Step) -> dict:
    return {
        "action": step.call.action,
        "narrative": step.call.narrative,
        "resource": step.call.resource,
        "counterparty": step.call.counterparty,
        "amount": usd(step.call.amount_cents) if step.call.amount_cents else "",
        "amount_cents": step.call.amount_cents,
        "sources": [
            {"origin": s.origin, "locator": s.locator, "excerpt": _excerpt(s.text)}
            for s in step.call.sources
        ],
        "outcome": step.outcome,
        "effect": step.effect,
        "reasons": list(step.reasons),
        "detail": step.detail,
        "checks": [
            {"check_id": c.check_id, "status": c.status, "detail": c.detail}
            for c in step.checks
        ],
    }


def result_json(result: ScenarioResult) -> dict:
    s = result.scenario
    return {
        "key": s.key,
        "group": s.group,
        "title": s.title,
        "question": s.question,
        "ungoverned_story": s.ungoverned,
        "governed_story": s.governed,
        "matched": result.matched_expectation,
        "prevented": usd(result.prevented_cents) if result.prevented_cents else "",
        "prevented_cents": result.prevented_cents,
        "lanes": {
            "ungoverned": {
                "steps": [_step_json(x) for x in result.ungoverned.steps],
                "executed": result.ungoverned.executed,
                "blocked": result.ungoverned.blocked,
                "held": result.ungoverned.held,
                "cash_moved": usd(result.ungoverned.ledger.cash_moved_cents),
                "unauthorized": usd(result.ungoverned.unauthorized_cents),
            },
            "governed": {
                "steps": [_step_json(x) for x in result.governed.steps],
                "executed": result.governed.executed,
                "blocked": result.governed.blocked,
                "held": result.governed.held,
                "cash_moved": usd(result.governed.ledger.cash_moved_cents),
                "unauthorized": usd(result.governed.unauthorized_cents),
            },
        },
    }


def summary(results: list[ScenarioResult]) -> dict:
    """The three numbers a board asks for, and nothing else."""
    prevented = sum(r.prevented_cents for r in results)
    held = sum(r.governed.held for r in results)
    blocked = sum(r.governed.blocked for r in results)
    ungoverned_bad = len([r for r in results if r.ungoverned.unauthorized_cents])
    return {
        "scenarios": len(results),
        "prevented": usd(prevented),
        "prevented_cents": prevented,
        "blocked": blocked,
        "held_for_approval": held,
        "incidents_ungoverned": ungoverned_bad,
        "incidents_governed": len([r for r in results if r.governed.unauthorized_cents]),
        "all_as_expected": all(r.matched_expectation for r in results),
    }
