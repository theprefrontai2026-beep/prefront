"""The objects the enforcement plane signs, checks and records.

Every type here is FROZEN, for a reason stronger than the usual one. The
spec's design rule is that "nothing the model outputs can widen its own
permissions", and the Policy Decision Service is the component that holds that
line. A PDS that could mutate the Mission it is evaluating — even by accident,
even helpfully, even to normalize a field — would be a component the model can
reach through its own arguments. So a Mission arrives frozen and leaves frozen,
and every check is a pure function of what it was handed.

Three objects carry the whole enforcement story:

  Mission             what the USER approved, signed by the Mission Authority.
                      The only thing that can widen it is another Mission.
  ActionAttestation   what the MODEL claims it is about to do, signed by the
                      Intent Binder inside the agent process. A claim, never a
                      permission: the PDS is what turns one into a decision.
  Decision            what the PDS concluded, recorded with the reasons.

The asymmetry between the first two is the product. An attestation is signed by
a key the agent process holds, so the agent can say anything it likes; what it
cannot do is make the PDS agree. Treating an attestation as authorization
anywhere in this package would collapse the two planes into one, which is
exactly the failure the spec's "unbounded delegation" row describes.

Time is INTEGER EPOCH SECONDS everywhere, never a datetime and never a string.
Timestamps go inside hashed, signed bodies, and a timezone-aware datetime has
several correct textual forms — two of which hash differently. An integer has
one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from .canonical import (
    TAG_ATTESTATION,
    TAG_MISSION,
    CanonicalizationError,
    digest,
)

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

# What the PDS can conclude. Deliberately three values, not two: the spec's
# step-up is not a soft deny, it is a decision to PAUSE ONE BRANCH and ask the
# human who signed the Mission about a specific delta. Collapsing it into deny
# is what forces customers to choose between a blocked agent and a wide Mission.
Effect = Literal["allow", "deny", "step_up"]

# Per-check outcome. The same three words the judgement plane's verdict
# contract uses (`eval-engine/evalengine/contract.py`), and that is not
# cosmetic: the spec plans to promote "per-call integrity checks inline as a
# PDS plugin" once they are proven out of band. A check that already speaks
# this language moves by registration rather than by rewrite.
Status = Literal["satisfied", "violated", "indeterminate"]

# Provenance class of one piece of evidence the model acted on. The enforcement
# plane consumes this to run its injection tripwire; the judgement plane
# RECOMPUTES it from the shipped content rather than trusting it, and reports
# an `evidence_mismatch` when the two disagree. That audit is the reason this
# field is a claim on an attestation rather than a fact in the control zone —
# see the spec's "Evidence labelling in the runtime" risk row.
Origin = Literal["trusted", "semi_trusted", "untrusted"]

# How much damage an action class can do. Set by the deployment's action-class
# registry, never inferred here. The PDS reads it for one purpose only: the
# injection tripwire denies HIGH blast radius actions whose evidence chain
# includes untrusted-origin content, and leaves low ones alone.
BlastRadius = Literal["low", "medium", "high"]


class ContractError(ValueError):
    """An object is malformed, so it cannot be signed, stored or evaluated."""


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    """A spend ceiling for one task tree.

    Money is INTEGER MINOR UNITS (cents, pence, satoshi) plus a currency code.
    Floats are refused outright rather than normalized, because a budget is
    both hashed into the Mission and compared with `>` at decision time, and
    binary floating point is wrong for both jobs in different ways: 0.1 + 0.2
    does not equal 0.3 when you compare it, and `1.0` versus `1` is a hashing
    problem `canonical.py` can only paper over.

    `max_calls` bounds the tree independently of money, because the spend
    ceiling does nothing about an agent that reads ten thousand records for
    free — the "over-broad retrieval" failure has no price tag.
    """

    amount_minor: int = 0
    currency: str = ""
    max_calls: int = 0

    def __post_init__(self) -> None:
        for name in ("amount_minor", "max_calls"):
            value = getattr(self, name)
            # bool is an int in Python; a `True` budget is a bug, not a ceiling.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(
                    f"Budget.{name} must be an integer (minor units / a count), "
                    f"got {type(value).__name__}. Money as a float is wrong for "
                    "both jobs a budget has: hashing it and comparing it"
                )
            if value < 0:
                raise ContractError(f"Budget.{name} cannot be negative, got {value}")
        if self.amount_minor and not self.currency:
            raise ContractError(
                "Budget.amount_minor is set without a currency: an unlabelled "
                "amount cannot be compared with a spend"
            )

    @property
    def caps_spend(self) -> bool:
        """Whether this budget constrains money at all.

        A zero amount means UNCAPPED, not "may spend nothing" — a Mission that
        authorizes only read actions has no meaningful amount, and reading the
        default as a hard zero would deny every such call for want of a
        currency nobody set. `max_calls` follows the same convention.
        """
        return self.amount_minor > 0

    @property
    def caps_calls(self) -> bool:
        return self.max_calls > 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "max_calls": self.max_calls,
        }


# ---------------------------------------------------------------------------
# Mission
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mission:
    """One consent screen, captured as a signed object.

    This is the user's approval of a TASK, and every field is a boundary the
    agent cannot cross by talking. The vocabulary in `action_classes`,
    `resources` and `counterparties` belongs to the DEPLOYMENT, never to this
    package — Prefront's defining principle is that the engine names no domain,
    and a Mission is where a customer's own nouns enter the system.

    An empty collection means UNCONSTRAINED on that axis, which is the only
    honest default for a field a consent screen may not have collected. It is
    also the field most worth narrowing: the spec's "effective versus used"
    report exists to turn each empty tuple into the short list the agent
    actually exercised.
    """

    mission_id: str
    # The end user, and the only field in this package sourced from outside it.
    # "`sub` only ever comes from the customer's IdP" — Warrant is not a second
    # identity store, so nothing here mints, validates or rewrites a subject.
    subject: str
    issuer: str
    instruction_hash: str

    action_classes: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    counterparties: tuple[str, ...] = ()

    budget: Budget = field(default_factory=Budget)

    # Validity window, epoch seconds. `not_after` is what makes a Mission a
    # TASK boundary rather than a session: the spec's whole complaint about
    # OAuth tokens is that they carry identity and scope "for as long as the
    # token lives", with no notion of the task ending.
    not_before: int = 0
    not_after: int = 0

    # Sub-agent depth cap. 0 means the root agent only — a Mission that does
    # not say "you may delegate" does not authorize delegation, which is the
    # opposite of how a bare OAuth token behaves.
    max_depth: int = 0

    issued_at: int = 0
    # Set when this Mission replaces an earlier one mid-task (the user widened
    # or narrowed the task). The Authority refuses to honour the superseded id
    # afterwards, so a captured older Mission is not a second live permission.
    supersedes: str = ""

    def __post_init__(self) -> None:
        for name in ("mission_id", "subject", "issuer", "instruction_hash"):
            if not getattr(self, name):
                raise ContractError(f"Mission.{name} is required and cannot be empty")
        if self.not_after and self.not_before and self.not_after <= self.not_before:
            raise ContractError(
                f"Mission validity window is empty or inverted: "
                f"not_before={self.not_before} not_after={self.not_after}"
            )
        if self.max_depth < 0:
            raise ContractError(f"Mission.max_depth cannot be negative, got {self.max_depth}")

    def to_payload(self) -> dict[str, Any]:
        """The exact body that gets hashed and signed.

        Field order is irrelevant (`canonical_json` sorts), but MEMBERSHIP is
        not: a field added here changes every future signature and must be
        added to every published verifier in the same release, or a
        conformant verifier in another language will reject our Missions.
        """
        return {
            "mission_id": self.mission_id,
            "subject": self.subject,
            "issuer": self.issuer,
            "instruction_hash": self.instruction_hash,
            "action_classes": list(self.action_classes),
            "resources": list(self.resources),
            "counterparties": list(self.counterparties),
            "budget": self.budget.to_payload(),
            "not_before": self.not_before,
            "not_after": self.not_after,
            "max_depth": self.max_depth,
            "issued_at": self.issued_at,
            "supersedes": self.supersedes,
        }

    def content_digest(self) -> str:
        return digest(TAG_MISSION, self.to_payload())

    # -- membership tests -------------------------------------------------
    #
    # All three read the same way on purpose: an EMPTY list is unconstrained,
    # a non-empty list is exhaustive. Written out separately rather than as one
    # helper because each one's emptiness means something different to a
    # reviewer reading a consent screen, and a shared helper would invite a
    # future caller to add a fourth axis without thinking about that.

    def permits_action(self, action_class: str) -> bool:
        return not self.action_classes or action_class in self.action_classes

    def permits_resource(self, resource: str) -> bool:
        return not self.resources or resource in self.resources

    def permits_counterparty(self, counterparty: str) -> bool:
        """Counterparties are the axis a step-up most often turns on: paying a
        NEW payee is the canonical delta a user should see under their original
        instruction rather than have denied outright."""
        return not self.counterparties or counterparty in self.counterparties

    def is_live_at(self, now: int) -> bool:
        """Window check only. Says nothing about revocation or supersession —
        those live in the task tree and the Authority respectively, because
        both can change after a Mission is signed and a signed object must
        never claim to know something that mutable."""
        if self.not_before and now < self.not_before:
            return False
        if self.not_after and now >= self.not_after:
            return False
        return True


@dataclass(frozen=True)
class SignedMission:
    """A Mission plus the Authority's signature over its canonical bytes.

    Kept as a separate type rather than as two more fields on `Mission` so that
    it is impossible to construct a Mission that carries its own signature —
    a shape that invites code to check `mission.signature is not None` and call
    that verification. Verification is a function of a KEY, and the key lives
    somewhere this object does not.
    """

    mission: Mission
    key_id: str
    signature: str  # base64url, no padding
    algorithm: str = "Ed25519"

    def __post_init__(self) -> None:
        for name in ("key_id", "signature"):
            if not getattr(self, name):
                raise ContractError(f"SignedMission.{name} is required")


# ---------------------------------------------------------------------------
# Action Attestation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRef:
    """One piece of content the model says it acted on.

    `content_hash` refs a payload in the tenant's evidence store; the hash is
    what travels through the control zone. `origin` is the runtime's CLAIM
    about where the content came from, and the judgement plane recomputes it
    independently — this field is the input to the enforcement plane's
    tripwire and the subject of the judgement plane's `evidence_mismatch`
    check at the same time.
    """

    content_hash: str
    origin: Origin = "untrusted"
    # Free-text pointer for a human reading the timeline ("tool:fetch_page#2").
    # Never parsed, never matched on — it exists so an incident review does not
    # have to resolve a hash against the evidence store to get oriented.
    locator: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            raise ContractError("EvidenceRef.content_hash is required")
        if self.origin not in ("trusted", "semi_trusted", "untrusted"):
            raise ContractError(
                f"EvidenceRef.origin must be trusted|semi_trusted|untrusted, "
                f"got {self.origin!r}"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "origin": self.origin,
            "locator": self.locator,
        }


@dataclass(frozen=True)
class ActionAttestation:
    """What the model proposes, signed by the Intent Binder in the agent process.

    The spec's buyer question for this object is "was this call what the model
    actually proposed, on what input?" — so the signature covers the ARGUMENT
    HASH, not the arguments. The gateway hashes what it actually received and
    compares; a proxy or a compromised tool that rewrites arguments in flight
    breaks the match without ever seeing the signing key.

    `tree_id` and `node_id` are the join key for everything downstream. The
    spec's claim that "one task reads as one story from consent to outcome"
    reduces to these two fields being stamped identically by the enforcement
    plane, the evidence store and the judgement plane.
    """

    tree_id: str
    node_id: str
    action: str          # the deployment's action class, e.g. its own "<noun>.<verb>"
    args_hash: str
    # Which step of the model's own stated plan this call claims to be. Not
    # verified on the path — the judgement plane's goal-alignment check is what
    # tests it against the session — but signed, so the claim is attributable.
    plan_step: str = ""
    resource: str = ""
    counterparty: str = ""
    # Money this specific call intends to move, in the Budget's minor units.
    # Zero for every non-spending call, which is most of them.
    amount_minor: int = 0
    evidence: tuple[EvidenceRef, ...] = ()
    issued_at: int = 0

    def __post_init__(self) -> None:
        for name in ("tree_id", "node_id", "action", "args_hash"):
            if not getattr(self, name):
                raise ContractError(
                    f"ActionAttestation.{name} is required: an attestation "
                    "missing it cannot be tied back to a task or an input"
                )
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise ContractError("ActionAttestation.amount_minor must be an integer")
        if self.amount_minor < 0:
            raise ContractError(
                f"ActionAttestation.amount_minor cannot be negative, got "
                f"{self.amount_minor}: a refund is its own action class, not a "
                "negative spend, or it would silently restore budget"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "tree_id": self.tree_id,
            "node_id": self.node_id,
            "action": self.action,
            "args_hash": self.args_hash,
            "plan_step": self.plan_step,
            "resource": self.resource,
            "counterparty": self.counterparty,
            "amount_minor": self.amount_minor,
            "evidence": [e.to_payload() for e in self.evidence],
            "issued_at": self.issued_at,
        }

    def content_digest(self) -> str:
        return digest(TAG_ATTESTATION, self.to_payload())

    def worst_origin(self) -> Origin:
        """The least trustworthy origin anywhere in the evidence chain.

        The tripwire needs the WORST, not a majority or an average: one
        injected web page among nine trusted documents is still an injected web
        page, and the spec's question is "what stops a web page from spending
        my money?"
        """
        if any(e.origin == "untrusted" for e in self.evidence):
            return "untrusted"
        if any(e.origin == "semi_trusted" for e in self.evidence):
            return "semi_trusted"
        return "trusted"


@dataclass(frozen=True)
class SignedAttestation:
    """An attestation plus the Intent Binder's signature.

    Separate from `SignedMission` despite the identical shape, because the two
    are trusted for completely different things and a shared type would make a
    mix-up type-check. The Mission signature proves a HUMAN approved a task;
    this one proves only which agent process made a claim.
    """

    attestation: ActionAttestation
    key_id: str
    signature: str
    algorithm: str = "Ed25519"

    def __post_init__(self) -> None:
        for name in ("key_id", "signature"):
            if not getattr(self, name):
                raise ContractError(f"SignedAttestation.{name} is required")


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """One PDS check's conclusion about one call.

    Mirrors the judgement plane's verdict shape on purpose (see `Status`), with
    one addition: `on_violation`, which says what THIS check failing does to
    the call. Out of band an effect is advisory and the combinator resolves
    precedence later; on the path there is no later.

    The two values map one-for-one onto the judgement plane's own `block` and
    `approval_required`, which is what makes the spec's planned promotion of
    integrity checks "inline as a PDS plugin" a registration rather than a
    rewrite. They are genuinely different outcomes, not severities: a forged
    signature is never something a human should be asked to wave through, while
    a new counterparty is exactly that — the user sees the delta under their
    original instruction and decides.

    `indeterminate` is a real outcome here, not an error. A check that cannot
    see what it needs must say so rather than guess, and the PDS resolves
    indeterminacy by FAILING CLOSED — the same convention the inline governance
    engine already uses, where a gating rule whose symbol is missing fail-safes
    to approval rather than to allow.
    """

    check_id: str
    status: Status
    detail: str = ""
    # What this check failing does. "deny" for anything that would mean the
    # call is not what it claims to be; "step_up" for a boundary a human can
    # legitimately extend mid-task.
    on_violation: Effect = "deny"

    def __post_init__(self) -> None:
        if not self.check_id:
            raise ContractError("CheckResult.check_id is required")
        if self.status not in ("satisfied", "violated", "indeterminate"):
            raise ContractError(f"CheckResult.status invalid: {self.status!r}")
        if self.on_violation not in ("deny", "step_up"):
            raise ContractError(
                f"CheckResult.on_violation must be deny|step_up, got "
                f"{self.on_violation!r}: 'allow' would be a check that does "
                "nothing when it fails"
            )


@dataclass(frozen=True)
class Decision:
    """What the PDS concluded, and enough of why to defend it later.

    Carries `mission_id`, `tree_id` and `node_id` so a recorded decision is
    self-contained: the spec promises evidence packs are "generated, not
    assembled", and a decision that has to be joined against three other
    stores to be understood is assembly.
    """

    effect: Effect
    tree_id: str
    node_id: str
    mission_id: str
    checks: tuple[CheckResult, ...] = ()
    # Machine-readable reason codes, not prose. These end up in a SIEM feed and
    # on an auditor's control-ID mapping, both of which match on the code.
    reasons: tuple[str, ...] = ()
    # Populated only on step_up: the specific delta to show the user under
    # their original instruction. A step-up screen that cannot name what
    # changed is just a second consent screen, which is how approval fatigue
    # starts.
    step_up_delta: tuple[str, ...] = ()
    decided_at: int = 0
    # Version of the policy bundle this was decided under. The spec's Prove
    # stage stamps it on every record so a decision can be replayed against the
    # rules that were actually live at the time.
    policy_version: str = ""

    def __post_init__(self) -> None:
        if self.effect not in ("allow", "deny", "step_up"):
            raise ContractError(f"Decision.effect invalid: {self.effect!r}")

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"

    def to_payload(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "tree_id": self.tree_id,
            "node_id": self.node_id,
            "mission_id": self.mission_id,
            "checks": [
                {
                    "check_id": c.check_id,
                    "status": c.status,
                    "detail": c.detail,
                    "on_violation": c.on_violation,
                }
                for c in self.checks
            ],
            "reasons": list(self.reasons),
            "step_up_delta": list(self.step_up_delta),
            "decided_at": self.decided_at,
            "policy_version": self.policy_version,
        }
