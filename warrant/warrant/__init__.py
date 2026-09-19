"""Warrant — the enforcement plane of Prefront.

Authorize (here), judge (`eval-engine`), prove (the trace both share). This
package is the first half: every side-effect call an agent makes is checked,
before it happens, against a signed human decision.

The reading order that makes the design make sense:

  canonical.py  the hashing both trust zones must agree on, byte for byte
  contract.py   Mission, ActionAttestation, Decision — what gets signed
  signing.py    Ed25519 and the JWKS that makes verification work offline
  registry.py   the deployment's own action classes (the engine names none)
  tree.py       the task tree: narrowing grants, shared budget, revocation
  authority.py  signs Missions; retires them when the user changes their mind
  binder.py     signs attestations inside the agent process
  pds.py        the deterministic allow / deny / step-up

Two invariants hold across all of it, and nothing in this package is allowed to
weaken either: **nothing the model outputs can widen its own permissions**, and
**evaluation never mutates state** — the PDS is a pure function so a proposed
policy can be replayed against recorded attestations.
"""

from .canonical import args_hash, canonical_json, content_hash, digest, instruction_hash
from .contract import (
    ActionAttestation,
    Budget,
    CheckResult,
    ContractError,
    Decision,
    EvidenceRef,
    Mission,
    SignedAttestation,
    SignedMission,
)
from .authority import MissionAuthority
from .binder import IntentBinder
from .pds import DecisionRequest, PolicyDecisionService
from .registry import ActionClass, ActionRegistry, UnknownActionClass
from .signing import KeySet, SigningKey, VerificationError, VerifyKey
from .tree import BudgetExceeded, Grant, TaskNode, TaskTree, TreeError, TreeStore

__all__ = [
    "ActionAttestation", "ActionClass", "ActionRegistry", "Budget", "BudgetExceeded",
    "CheckResult", "ContractError", "Decision", "DecisionRequest", "EvidenceRef",
    "Grant", "IntentBinder", "KeySet", "Mission", "MissionAuthority",
    "PolicyDecisionService", "SignedAttestation", "SignedMission", "SigningKey",
    "TaskNode", "TaskTree", "TreeError", "TreeStore", "UnknownActionClass",
    "VerificationError", "VerifyKey", "args_hash", "canonical_json", "content_hash",
    "digest", "instruction_hash",
]
