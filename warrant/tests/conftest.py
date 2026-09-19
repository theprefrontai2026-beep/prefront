"""Shared fixtures.

The vocabulary below (`vault.read`, `ledger.transfer`, …) is INVENTED for the
tests and belongs to no deployment, real or bundled. Prefront's defining
principle is that the engine names no domain, and a test suite that reached for
one of the repo's demo vocabularies would make this package look like it knew
about that demo — see `test_domain_independence.py`, which enforces exactly
that for `warrant/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from warrant import (  # noqa: E402
    ActionClass,
    ActionRegistry,
    Budget,
    IntentBinder,
    MissionAuthority,
    PolicyDecisionService,
    SigningKey,
    TreeStore,
)

# A fixed "now" so nothing in the suite depends on the wall clock. Every
# Mission window below is expressed relative to this.
NOW = 1_000_000


@pytest.fixture
def registry() -> ActionRegistry:
    return ActionRegistry(
        [
            ActionClass("vault.read", blast_radius="low", side_effect=False),
            ActionClass("record.update", blast_radius="medium", side_effect=True),
            # The high-radius one: the tripwire only fires on these.
            ActionClass("ledger.transfer", blast_radius="high", side_effect=True),
        ],
        version="test-registry-1",
    )


@pytest.fixture
def authority() -> MissionAuthority:
    return MissionAuthority("authority.test", SigningKey.generate("ma-1"))


@pytest.fixture
def agent_key() -> SigningKey:
    return SigningKey.generate("agent-1")


@pytest.fixture
def world(authority, agent_key, registry):
    """A signed Mission, a live tree, a Binder and a PDS, already wired together.

    Returned as one object because every interesting test needs all five and
    asserting on how they interact is the point — a fixture per component would
    make each test reassemble the same world.
    """

    class World:
        def __init__(self) -> None:
            self.authority = authority
            self.registry = registry
            self.agent_key = agent_key
            self.subject = "user-42"
            self.signed_mission = authority.issue(
                mission_id="mission-1",
                subject=self.subject,
                instruction="Settle the outstanding items for this quarter.",
                action_classes=("vault.read", "record.update", "ledger.transfer"),
                resources=("folder-a",),
                counterparties=("counterparty-known",),
                budget=Budget(amount_minor=50_000, currency="USD", max_calls=20),
                not_before=NOW - 100,
                not_after=NOW + 3600,
                max_depth=1,
                issued_at=NOW - 100,
            )
            self.mission = self.signed_mission.mission
            self.trees = TreeStore()
            self.tree = self.trees.create("tree-1", self.mission, "root-agent", created_at=NOW)
            self.binder = IntentBinder(agent_key, "tree-1", self.tree.root_id)
            self.pds = PolicyDecisionService(
                trees=self.trees,
                agent_keys=KEYSET_FOR(agent_key),
                registry=registry,
                policy_version="test-policy-1",
            )

    return World()


def KEYSET_FOR(key: SigningKey):
    from warrant import KeySet

    return KeySet([key.verify_key()])


@pytest.fixture
def request_for(world):
    """Build a DecisionRequest for an attestation, with the honest defaults.

    Defaults to the arguments actually attested and the real subject, so each
    test changes only the ONE thing it is about — a test that has to restate
    four correct values to vary a fifth hides which one mattered.
    """
    from warrant import DecisionRequest

    def build(signed, args, *, node_id=None, subject=None, now=NOW):
        return DecisionRequest(
            signed=signed,
            observed_args=args,
            node_id=node_id if node_id is not None else signed.attestation.node_id,
            now=now,
            token_subject=world.subject if subject is None else subject,
        )

    return build
