"""The Treasury Operations agent, and the two lanes it runs in.

The agent here is SCRIPTED, not a language model, and that is a deliberate
choice rather than a shortcut. What is being demonstrated is the enforcement
plane, which is deterministic by construction — the spec's whole thesis is that
the runtime never asks a model for permission. Putting a live model in the loop
would add variance to the one part of the system that is supposed to have none,
and would make a demo that behaves differently on stage than in rehearsal.

What the script encodes is a real model's behaviour, not a caricature: on a
poisoned invoice the agent does exactly what a competent model does — it reads
a document, believes it, and faithfully proposes the action the document asked
for. That is the failure. No agent in this demo is malicious, buggy or badly
prompted; each one is doing its job on the information it was given.

Two lanes run the identical call sequence:

  UNGOVERNED  the agent's proposal IS the action. This is how almost every
              agent in production works today.
  GOVERNED    the same proposal is attested and put to the PDS, and only a
              decision of `allow` reaches the world.

Everything else — the tools, the data, the sequence — is held constant, so any
difference between the two columns is attributable to the enforcement plane and
to nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from warrant import DecisionRequest

from world import INVOICES, SUPPLIERS, Ledger, usd

Origin = Literal["trusted", "semi_trusted", "untrusted"]


@dataclass(frozen=True)
class Source:
    """One thing the agent derived a call from.

    The distinction this demo turns on is not "did the agent read a document"
    but "did the agent ACT on one". An agent settling an invoice from the ERP
    record cites a trusted source even though it also read the supplier's PDF;
    an injected agent cites the PDF, because that is genuinely where its
    parameters came from. The tripwire reads that difference.
    """

    text: str
    origin: Origin
    locator: str


@dataclass(frozen=True)
class ToolCall:
    """One thing the agent proposes to do."""

    action: str
    args: dict
    narrative: str
    resource: str = ""
    counterparty: str = ""
    amount_cents: int = 0
    sources: tuple[Source, ...] = ()
    # The portion of this call that should never have happened, in cents.
    # Declared by the scenario rather than inferred, because "unauthorized" is
    # a judgement about the business situation — an over-budget payment to a
    # real supplier for real work is a control firing, not a loss.
    unauthorized_cents: int = 0
    # Set only by scenarios that model an attack on the CALL rather than on the
    # agent: a proxy rewriting arguments in flight, or a replayed attestation.
    tamper_args: Optional[dict] = None
    replay_from_node: str = ""


@dataclass
class Step:
    """What happened to one call, in one lane."""

    call: ToolCall
    outcome: Literal["executed", "blocked", "held_for_approval"]
    effect: str = ""            # governed lane only: allow | deny | step_up
    reasons: tuple[str, ...] = ()
    detail: str = ""
    checks: tuple = ()

    @property
    def moved_money(self) -> bool:
        return self.outcome == "executed" and self.call.amount_cents > 0


def _apply(call: ToolCall, ledger: Ledger) -> None:
    """Let the call touch the world. Both lanes share this, so the governed
    lane cannot be accused of a different execution path — only of reaching it
    less often."""
    if call.action == "ap.payment.release":
        ledger.release_payment(call.counterparty, call.amount_cents,
                               f"{call.resource}: {usd(call.amount_cents)}")
    elif call.action == "ap.supplier.create":
        ledger.create_supplier(call.counterparty, call.narrative)
    elif call.action == "ap.invoice.annotate":
        ledger.annotate(call.counterparty, call.narrative)


def run_ungoverned(calls: list[ToolCall], ledger: Ledger) -> list[Step]:
    """No enforcement plane. The agent proposes; the world complies."""
    steps: list[Step] = []
    for call in calls:
        _apply(call, ledger)
        steps.append(Step(call=call, outcome="executed", detail="no control on the path"))
    return steps


def run_governed(
    calls: list[ToolCall],
    ledger: Ledger,
    dep,
    now: Optional[int] = None,
    subject: Optional[str] = None,
) -> list[Step]:
    """The same sequence, through Warrant.

    Note the reserve/settle pair around execution. The PDS decides without
    touching the ledger — it is a pure function — so the budget is only
    actually consumed by the component that executes, and a call denied by a
    later control never charges the task.
    """
    from deployment import NOW

    at = NOW if now is None else now
    steps: list[Step] = []

    for call in calls:
        binder = dep.binder
        node_id = dep.tree.root_id

        # A sub-agent call is attested from that node, which is what makes the
        # narrowed grant apply to it.
        if call.args.get("_sub_agent_node"):
            node_id = call.args["_sub_agent_node"]
            binder = dep.binder.for_node(node_id)

        attested_args = {k: v for k, v in call.args.items() if not k.startswith("_")}
        signed = binder.attest(
            action=call.action,
            args=attested_args,
            plan_step=call.narrative,
            resource=call.resource,
            counterparty=call.counterparty,
            amount_minor=call.amount_cents,
            evidence=[
                binder.evidence(s.text, origin=s.origin, locator=s.locator) for s in call.sources
            ],
            issued_at=at,
        )

        # What the GATEWAY sees. Identical to the attested arguments unless the
        # scenario is modelling an in-flight rewrite.
        observed = call.tamper_args if call.tamper_args is not None else attested_args
        arriving_node = call.replay_from_node or node_id

        decision = dep.pds.decide(
            DecisionRequest(
                signed=signed,
                observed_args=observed,
                node_id=arriving_node,
                now=at,
                token_subject=dep.mission.subject if subject is None else subject,
            )
        )

        if decision.effect == "allow":
            handle = dep.tree.reserve(call.amount_cents, at=at)
            _apply(call, ledger)
            dep.tree.settle(handle)
            outcome = "executed"
        elif decision.effect == "step_up":
            outcome = "held_for_approval"
        else:
            outcome = "blocked"

        steps.append(
            Step(
                call=call,
                outcome=outcome,
                effect=decision.effect,
                reasons=decision.reasons,
                detail=" ".join(decision.step_up_delta)
                or next((c.detail for c in decision.checks if c.status != "satisfied"), ""),
                checks=decision.checks,
            )
        )
    return steps
