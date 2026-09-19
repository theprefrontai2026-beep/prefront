"""Wiring Arcadia's vocabulary into the enforcement engine.

This file is the whole integration. It imports `warrant`, hands it this
application's nouns, and gets back a PDS. Nothing here subclasses, patches or
extends the engine — if it did, the claim that the same engine governs a
different application unchanged would not survive its first customer.

Read it as the answer to "what would my team have to write?": one consent
screen's worth of fields, one action-class list, and a key. Everything else is
the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from warrant import (
    Budget,
    IntentBinder,
    KeySet,
    MissionAuthority,
    PolicyDecisionService,
    SigningKey,
    TreeStore,
)

from world import ACTIONS, APPROVED_COUNTERPARTIES, CURRENCY, INVOICES, dollars

# A fixed clock. Every timestamp in the demo is relative to this, so two runs a
# week apart produce identical output — a demo that drifts with the wall clock
# is a demo that behaves differently on stage than it did in rehearsal.
NOW = 1_772_000_000  # a Tuesday, 02:13 local: the unattended overnight window
WINDOW_OPENS = NOW - 3600
WINDOW_CLOSES = NOW + 4 * 3600

OPERATOR = "t.okafor@arcadia.example"
AGENT = "treasury-ops-agent"

# What the human actually approved, in their words. This exact string is
# hashed into the Mission, so the consent screen and the signed object cannot
# drift apart — the engine hashes the instruction itself rather than accepting
# a hash, precisely so this stays true.
INSTRUCTION = (
    "Settle the March supplier invoices overnight, up to $250,000 in total, "
    "to our established suppliers. Flag anything unusual for me in the morning."
)

BUDGET_CENTS = dollars(250_000)


@dataclass
class Deployment:
    """One fully-wired Arcadia deployment: consent, tree, keys, decisions."""

    authority: MissionAuthority
    signed_mission: object
    trees: TreeStore
    tree: object
    pds: PolicyDecisionService
    binder: IntentBinder
    agent_key: SigningKey

    @property
    def mission(self):
        return self.signed_mission.mission


def build(tree_id: str = "arcadia-run-1", subject: str = OPERATOR) -> Deployment:
    """Everything a customer sets up once, per task.

    In production the Authority's key lives in a KMS and the agent's key is
    provisioned to the agent process; both are generated here because a demo
    that shipped a private key in a repository would be teaching the wrong
    lesson.
    """
    authority = MissionAuthority("arcadia.treasury.authority", SigningKey.generate("arcadia-ma-1"))
    agent_key = SigningKey.generate("arcadia-agent-1")

    signed_mission = authority.issue(
        mission_id="arcadia-march-settlement",
        subject=subject,
        instruction=INSTRUCTION,
        # Note what is ABSENT: `ap.supplier.create`. The operator approved
        # settling invoices, not onboarding counterparties, and a Mission that
        # listed every verb the agent's tools expose would be a Mission that
        # bounded nothing.
        action_classes=(
            "ap.invoice.read",
            "ap.supplier.lookup",
            "ap.invoice.annotate",
            "ap.payment.release",
        ),
        resources=tuple(INVOICES),          # the March batch, and nothing else
        counterparties=APPROVED_COUNTERPARTIES,
        budget=Budget(amount_minor=BUDGET_CENTS, currency=CURRENCY, max_calls=40),
        not_before=WINDOW_OPENS,
        not_after=WINDOW_CLOSES,
        max_depth=1,                        # one tier of sub-agents, no deeper
        issued_at=WINDOW_OPENS,
    )
    authority.verify(signed_mission)

    trees = TreeStore()
    tree = trees.create(tree_id, signed_mission.mission, AGENT, created_at=NOW)
    pds = PolicyDecisionService(
        trees=trees,
        agent_keys=KeySet([agent_key.verify_key()]),
        registry=ACTIONS,
        policy_version="arcadia-ap-policy-7",
    )
    return Deployment(
        authority=authority,
        signed_mission=signed_mission,
        trees=trees,
        tree=tree,
        pds=pds,
        binder=IntentBinder(agent_key, tree_id, tree.root_id),
        agent_key=agent_key,
    )
