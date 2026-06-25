"""Shared dataclasses for the governance pipeline.

``GovernanceContext`` is the per-call state each stage enriches; a stage either
adds to it or short-circuits by setting ``decision``. New concerns (authN,
external engines, approval workflow) are new stages over the same context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Caller:
    """The trusted caller identity — injected by config/identity, never by the agent.

    A generic ATTRIBUTE BAG: whatever columns the deployment's IDENTITY_QUERY
    returns become caller attributes (``caller.<attr>`` in rules, ``:caller_<attr>``
    in templates, ``caller_columns`` in writes). ``role`` and ``region`` are
    conventional contract names the query aliases onto its own schema — Prefront
    never assumes any particular identity table.
    """

    attrs: dict[str, Any] = field(default_factory=dict)
    source: str = "config"

    @property
    def role(self) -> Optional[str]:
        return self.attrs.get("role")

    @property
    def region(self) -> Optional[str]:
        return self.attrs.get("region")

    def as_dict(self) -> dict:
        return dict(self.attrs)


@dataclass
class RuleOutcome:
    """One rule evaluated against the facts."""

    rule_key: str
    decision: str                              # block | approval_required | mask | …
    fired: bool = False
    missing: list[str] = field(default_factory=list)   # symbols absent from facts
    reason: str = ""
    approver_role: Optional[str] = None
    restricted_fields: list[str] = field(default_factory=list)
    rule_type: str = ""                        # restriction | approval_threshold | data_access | …
    conditions: list[dict] = field(default_factory=list)  # the rule's clauses (verbatim from the bundle)
    source: dict = field(default_factory=dict)  # opaque provenance (text/evidence/document/section) — engine never interprets it

    @property
    def indeterminate(self) -> bool:
        return bool(self.missing)


@dataclass
class Decision:
    """The aggregated governance decision for one tool call."""

    status: str                                # allowed | blocked | approval_required
    reasons: list[str] = field(default_factory=list)
    approver_roles: list[str] = field(default_factory=list)
    mask_fields: list[str] = field(default_factory=list)
    outcomes: list[RuleOutcome] = field(default_factory=list)


@dataclass
class GovernanceContext:
    """Mutable per-call state threaded through the pipeline stages."""

    intent: str
    kind: str                                  # read | precheck
    args: dict[str, Any]
    caller: Optional[Caller] = None
    facts: dict[str, Any] = field(default_factory=dict)
    decision: Optional[Decision] = None
