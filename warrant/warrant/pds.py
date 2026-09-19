"""The Policy Decision Service: allow, deny or step up, deterministically.

This is the party the model cannot reach. Everything upstream of it — the
plan, the arguments, the attestation, the evidence labels — is something the
agent produced and can therefore lie about. The PDS's whole job is to be the
component whose answer does not depend on the agent being honest.

Three properties, each load-bearing for a different promise in the spec.

**It is a pure function.** `decide()` reads the tree, the Mission and the
registry, and mutates nothing. That is what makes the Test stage possible at
all: the sandbox "replays a proposed policy against last week's recorded
attestations", which only means anything if replaying the same inputs yields
the same decision. It is also why budget is checked with `would_exceed`
(a read) rather than by taking a reservation — the reservation belongs to
whoever is about to execute the call, not to the component deciding whether
they may.

**Every check runs, always.** There is no short-circuit on the first failure.
A decision that stopped at the first violated check would record one reason
when three applied, and the spec's Narrow stage compares what was permitted
with what was exercised — which needs the full picture of why a call was
refused, not the first excuse. The cost is a handful of comparisons against a
2 ms budget, which is not where the time goes.

**Indeterminate fails closed.** A check that cannot see what it needs says
`indeterminate`, and the resolver treats it as that check's violation. The
alternative — treating "I could not tell" as "fine" — means every gap in
capture silently becomes permission, which is the failure mode that makes an
evidence chain worthless precisely when it is needed.

Effect precedence is deny > step_up > allow, and it is defined ONCE, in
`_resolve`. A step-up is not a soft deny: it pauses one branch and shows a
human a specific delta. Collapsing the two would force customers to choose
between a blocked agent and a Mission wide enough never to need asking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .canonical import args_hash
from .contract import (
    ActionAttestation,
    CheckResult,
    Decision,
    Effect,
    Mission,
    SignedAttestation,
)
from .registry import ActionRegistry, UnknownActionClass
from .signing import KeySet, VerificationError
from .tree import TaskTree, TreeStore

# Check ids. Constants because they are matched on downstream — a SIEM rule, an
# auditor's control mapping and the judgement plane's promotion path all key on
# these strings, so they are wire format, not log text.
CHECK_ATTESTATION_SIGNATURE = "pds.attestation_signature"
CHECK_ATTESTATION_BINDING = "pds.attestation_binding"
CHECK_ARGUMENT_INTEGRITY = "pds.argument_integrity"
CHECK_SUBJECT_BINDING = "pds.subject_binding"
CHECK_TREE_LIVENESS = "pds.tree_liveness"
CHECK_DEPTH_CAP = "pds.depth_cap"
CHECK_ACTION_MEMBERSHIP = "pds.action_membership"
CHECK_RESOURCE_SCOPE = "pds.resource_scope"
CHECK_COUNTERPARTY_SCOPE = "pds.counterparty_scope"
CHECK_BUDGET = "pds.budget"
CHECK_CALL_CEILING = "pds.call_ceiling"
CHECK_INJECTION_TRIPWIRE = "pds.injection_tripwire"


@dataclass(frozen=True)
class DecisionRequest:
    """Everything the PDS is allowed to look at.

    Written as one frozen object rather than a long parameter list so that
    "what the PDS can see" is a reviewable list in one place. Adding a field
    here is a deliberate act — the failure this guards against is a check that
    quietly starts consulting ambient state and stops being replayable.

    `observed_args` is the arguments the GATEWAY received, not the ones the
    model says it sent. The difference between hashing these and trusting
    `attestation.args_hash` is the entire argument-integrity check.
    """

    signed: SignedAttestation
    observed_args: dict[str, Any]
    node_id: str
    now: int
    # The subject the calling token was issued to, resolved by the token
    # service from the customer's IdP. Never read off the attestation: the
    # agent signs that, and a subject the agent can choose is not an identity.
    token_subject: str = ""


class PolicyDecisionService:
    """Deterministic allow / deny / step-up.

    Holds no per-call state. The stores it references (trees, keys, registry)
    are inputs in every meaningful sense — two PDS replicas given the same
    stores must decide identically, or the spec's "denylist reaches every PDS"
    guarantee would be about propagation while the decisions still diverged.
    """

    def __init__(
        self,
        trees: TreeStore,
        agent_keys: KeySet,
        registry: ActionRegistry,
        policy_version: str = "",
    ) -> None:
        self.trees = trees
        self.agent_keys = agent_keys
        self.registry = registry
        self.policy_version = policy_version

    # -- entry point ------------------------------------------------------

    def decide(self, request: DecisionRequest) -> Decision:
        att = request.signed.attestation
        checks: list[CheckResult] = []

        # Resolving the tree is the one step that can fail before any check
        # runs: without a tree there is no Mission to check anything against.
        # An unknown or denylisted tree is a DENY with a reason, never an
        # exception — a gateway that has to catch an exception to learn a call
        # was refused will eventually catch it somewhere that swallows it.
        if self.trees.is_denied(att.tree_id):
            # Carry the tree's own revocation reason when this replica happens
            # to hold the tree. The denylist alone says only THAT a task was
            # stopped; an incident review needs to know why, and "spend
            # velocity anomaly" is a different story from "user pressed stop".
            # A replica that learned of the revocation by denylist merge has no
            # reason to report, and says so rather than inventing one.
            reason = ""
            try:
                reason = self.trees.get(att.tree_id).revoked_reason
            except Exception:
                reason = ""
            detail = f"tree {att.tree_id!r} is on the revocation denylist"
            detail += f" ({reason})" if reason else " (reason not held by this replica)"
            return self._deny_outright(att, CHECK_TREE_LIVENESS, detail, request.now)
        try:
            tree = self.trees.get(att.tree_id)
        except Exception:
            return self._deny_outright(
                att,
                CHECK_ATTESTATION_BINDING,
                f"unknown tree {att.tree_id!r}: no Mission to check this call against",
                request.now,
            )

        checks.append(self._check_signature(request))
        checks.append(self._check_binding(request, tree))
        checks.append(self._check_argument_integrity(request))
        checks.append(self._check_subject(request, tree.mission))
        checks.append(self._check_liveness(tree, request.now))

        node = self._safe_node(tree, request.node_id)
        checks.append(self._check_depth(tree, node))
        checks.append(self._check_action(tree, node, att))
        checks.append(self._check_resource(tree, node, att))
        checks.append(self._check_counterparty(tree, node, att))
        checks.append(self._check_call_ceiling(tree))
        checks.append(self._check_budget(tree, att))
        checks.append(self._check_tripwire(att))

        return self._resolve(att, tuple(checks), tree.mission, request.now)

    # -- individual checks ------------------------------------------------
    #
    # Each returns exactly one CheckResult and touches nothing else. They are
    # written to be readable side by side, because the set of them IS the
    # security review: a reviewer should be able to read this section top to
    # bottom and say what the PDS does and does not test.

    def _check_signature(self, request: DecisionRequest) -> CheckResult:
        """Was this attestation signed by a key we published for an agent?

        Proves only WHICH agent process made the claim — never that the claim
        is true. Every other check exists because this one proves so little.
        """
        signed = request.signed
        try:
            self.agent_keys.verify(
                signed.attestation.to_payload(),
                signed.key_id,
                signed.signature,
                signed.algorithm,
            )
        except VerificationError as exc:
            return CheckResult(
                check_id=CHECK_ATTESTATION_SIGNATURE,
                status="violated",
                detail=str(exc),
                on_violation="deny",
            )
        return CheckResult(CHECK_ATTESTATION_SIGNATURE, "satisfied", f"signed by {signed.key_id}")

    def _check_binding(self, request: DecisionRequest, tree: TaskTree) -> CheckResult:
        """Does the attestation name the node this call is actually arriving on?

        This is the replay check. A valid, correctly-signed attestation for one
        node, replayed against another, would otherwise pass every remaining
        test — the signature is genuine and the arguments match, because it is
        a real attestation. What it is not is a statement about THIS call.
        """
        att = request.signed.attestation
        if att.node_id != request.node_id:
            return CheckResult(
                check_id=CHECK_ATTESTATION_BINDING,
                status="violated",
                detail=(
                    f"attestation is bound to node {att.node_id!r} but arrived on "
                    f"{request.node_id!r}: a genuine attestation for another call "
                    "is a replay, not an authorization"
                ),
                on_violation="deny",
            )
        if att.tree_id != tree.tree_id:
            return CheckResult(
                check_id=CHECK_ATTESTATION_BINDING,
                status="violated",
                detail=f"attestation tree {att.tree_id!r} != {tree.tree_id!r}",
                on_violation="deny",
            )
        return CheckResult(CHECK_ATTESTATION_BINDING, "satisfied", f"bound to {att.node_id}")

    def _check_argument_integrity(self, request: DecisionRequest) -> CheckResult:
        """Are the arguments the gateway received the ones the model attested to?

        The spec's buyer question, verbatim: "was this call what the model
        actually proposed, on what input?" A rewriting proxy, a compromised
        tool or a confused-deputy retry all break this equality without ever
        touching the signing key.
        """
        att = request.signed.attestation
        try:
            observed = args_hash(request.observed_args)
        except Exception as exc:
            # Unhashable arguments are an indeterminate, not a violation: we
            # genuinely cannot tell whether they match. Fails closed via the
            # resolver, and says so, rather than asserting a mismatch that
            # would read as an accusation in an incident review.
            return CheckResult(
                check_id=CHECK_ARGUMENT_INTEGRITY,
                status="indeterminate",
                detail=f"observed arguments could not be canonicalized: {exc}",
                on_violation="deny",
            )
        if observed != att.args_hash:
            return CheckResult(
                check_id=CHECK_ARGUMENT_INTEGRITY,
                status="violated",
                detail=(
                    f"argument hash mismatch: attested {att.args_hash[:16]}…, "
                    f"observed {observed[:16]}…. The arguments changed between "
                    "the model proposing this call and the tool receiving it"
                ),
                on_violation="deny",
            )
        return CheckResult(CHECK_ARGUMENT_INTEGRITY, "satisfied", "arguments match the attestation")

    def _check_subject(self, request: DecisionRequest, mission: Mission) -> CheckResult:
        """Is the token's user the user who approved this Mission?

        `token_subject` comes from the token service, which got it from the
        customer's IdP. When it is absent we cannot tell, so this is
        indeterminate — and indeterminate fails closed. Treating a missing
        subject as a match would make the check optional in exactly the
        deployment that forgot to wire identity.
        """
        if not request.token_subject:
            return CheckResult(
                check_id=CHECK_SUBJECT_BINDING,
                status="indeterminate",
                detail=(
                    "no subject on the calling token: cannot confirm this call is "
                    "being made for the user who approved the Mission"
                ),
                on_violation="deny",
            )
        if request.token_subject != mission.subject:
            return CheckResult(
                check_id=CHECK_SUBJECT_BINDING,
                status="violated",
                detail=(
                    f"token subject {request.token_subject!r} is not the Mission's "
                    f"subject: this Mission is another user's consent"
                ),
                on_violation="deny",
            )
        return CheckResult(CHECK_SUBJECT_BINDING, "satisfied", "token subject matches the Mission")

    def _check_liveness(self, tree: TaskTree, now: int) -> CheckResult:
        """Is the task still running, and is the Mission still in its window?

        One check rather than two because the two are indistinguishable to the
        caller and to the user: an expired Mission and a revoked tree both mean
        "this task is over". Splitting them would invite a caller to test one.
        """
        if tree.revoked:
            reason = tree.revoked_reason or "revoked"
            return CheckResult(
                check_id=CHECK_TREE_LIVENESS,
                status="violated",
                detail=f"task tree {tree.tree_id!r} was revoked ({reason})",
                on_violation="deny",
            )
        if not tree.mission.is_live_at(now):
            return CheckResult(
                check_id=CHECK_TREE_LIVENESS,
                status="violated",
                detail=(
                    f"Mission {tree.mission.mission_id!r} is outside its validity "
                    f"window at {now} "
                    f"[{tree.mission.not_before or '-'}, {tree.mission.not_after or '-'})"
                ),
                on_violation="deny",
            )
        return CheckResult(CHECK_TREE_LIVENESS, "satisfied", "tree live, Mission in window")

    def _check_depth(self, tree: TaskTree, node) -> CheckResult:
        """Is this actor within the delegation depth the user approved?

        The tree refuses to `spawn` past the cap, so a node deeper than
        `max_depth` should be unreachable. Checked anyway, because "should be
        unreachable" describes the state of the code rather than the state of
        the world: a node restored from a store, or minted by a future code
        path, reaches the PDS the same way.
        """
        if node is None:
            return CheckResult(
                check_id=CHECK_DEPTH_CAP,
                status="indeterminate",
                detail="calling node is not in the tree; its depth cannot be established",
                on_violation="deny",
            )
        if node.depth > tree.mission.max_depth:
            return CheckResult(
                check_id=CHECK_DEPTH_CAP,
                status="violated",
                detail=(
                    f"node depth {node.depth} exceeds the Mission's max_depth of "
                    f"{tree.mission.max_depth}"
                ),
                on_violation="deny",
            )
        return CheckResult(CHECK_DEPTH_CAP, "satisfied", f"depth {node.depth}")

    def _check_action(self, tree: TaskTree, node, att: ActionAttestation) -> CheckResult:
        """Is this action class inside BOTH the Mission and this node's grant?

        Both, because they answer different questions. The Mission is what the
        user approved; the node grant is what survived narrowing on the way
        down to this sub-agent. A call that passes the first and fails the
        second is a sub-agent reaching past its delegation — which is the
        escalation the whole tree exists to prevent.
        """
        if node is None:
            return CheckResult(
                check_id=CHECK_ACTION_MEMBERSHIP,
                status="indeterminate",
                detail="calling node is not in the tree; its grant cannot be read",
                on_violation="deny",
            )
        if not tree.mission.permits_action(att.action):
            return CheckResult(
                check_id=CHECK_ACTION_MEMBERSHIP,
                status="violated",
                detail=(
                    f"action {att.action!r} is not among the action classes the "
                    "user approved for this task"
                ),
                on_violation="deny",
            )
        if not node.grant.permits_action(att.action):
            return CheckResult(
                check_id=CHECK_ACTION_MEMBERSHIP,
                status="violated",
                detail=(
                    f"action {att.action!r} is permitted by the Mission but not by "
                    f"node {node.node_id!r}'s narrowed grant: a sub-agent cannot "
                    "recover a permission its parent declined to delegate"
                ),
                on_violation="deny",
            )
        return CheckResult(CHECK_ACTION_MEMBERSHIP, "satisfied", f"{att.action} is in scope")

    def _check_resource(self, tree: TaskTree, node, att: ActionAttestation) -> CheckResult:
        """Is the target resource in scope? Absent means the call names none."""
        if not att.resource:
            return CheckResult(CHECK_RESOURCE_SCOPE, "satisfied", "call names no resource")
        if node is None:
            return CheckResult(
                CHECK_RESOURCE_SCOPE,
                "indeterminate",
                "calling node is not in the tree; its grant cannot be read",
                on_violation="deny",
            )
        if not tree.mission.permits_resource(att.resource) or not node.grant.permits_resource(
            att.resource
        ):
            return CheckResult(
                check_id=CHECK_RESOURCE_SCOPE,
                status="violated",
                detail=f"resource {att.resource!r} is outside the approved scope",
                on_violation="deny",
            )
        return CheckResult(CHECK_RESOURCE_SCOPE, "satisfied", f"{att.resource} is in scope")

    def _check_counterparty(self, tree: TaskTree, node, att: ActionAttestation) -> CheckResult:
        """Is this counterparty one the user approved?

        The one membership check that STEPS UP rather than denying. A new payee
        is the spec's own example of a delta worth showing a human: it is
        usually the task legitimately developing, not an attack, and denying it
        outright is what teaches users to approve Missions with an empty
        counterparty list — which removes the control entirely.
        """
        if not att.counterparty:
            return CheckResult(CHECK_COUNTERPARTY_SCOPE, "satisfied", "call names no counterparty")
        if node is None:
            return CheckResult(
                CHECK_COUNTERPARTY_SCOPE,
                "indeterminate",
                "calling node is not in the tree; its grant cannot be read",
                on_violation="step_up",
            )
        if not tree.mission.permits_counterparty(att.counterparty) or not (
            node.grant.permits_counterparty(att.counterparty)
        ):
            return CheckResult(
                check_id=CHECK_COUNTERPARTY_SCOPE,
                status="violated",
                detail=(
                    f"counterparty {att.counterparty!r} is not one the user "
                    "approved for this task"
                ),
                on_violation="step_up",
            )
        return CheckResult(CHECK_COUNTERPARTY_SCOPE, "satisfied", f"{att.counterparty} is approved")

    def _check_call_ceiling(self, tree: TaskTree) -> CheckResult:
        """Has the tree used its permitted number of calls?

        Denies rather than stepping up, unlike the spend ceiling. An exhausted
        call budget is almost always a loop — the judgement plane's "retry
        storm" — and asking a human to approve each iteration of a loop is how
        you get a human who approves everything.
        """
        if tree.calls_exhausted():
            return CheckResult(
                check_id=CHECK_CALL_CEILING,
                status="violated",
                detail=(
                    f"tree {tree.tree_id!r} has used its {tree.budget.max_calls} "
                    "permitted calls"
                ),
                on_violation="deny",
            )
        return CheckResult(CHECK_CALL_CEILING, "satisfied", f"{tree.call_count} calls so far")

    def _check_budget(self, tree: TaskTree, att: ActionAttestation) -> CheckResult:
        """Would this call take the tree past its spend ceiling?

        Read-only — `would_exceed`, never `reserve`. The reservation belongs to
        whoever executes the call; a PDS that reserved would charge the budget
        for calls that were then denied by a later check in this very list.
        """
        if not att.amount_minor:
            return CheckResult(CHECK_BUDGET, "satisfied", "call moves no money")
        if tree.would_exceed(att.amount_minor):
            remaining = tree.remaining_minor()
            return CheckResult(
                check_id=CHECK_BUDGET,
                status="violated",
                detail=(
                    f"call of {att.amount_minor} would exceed the task budget "
                    f"({remaining} {tree.budget.currency} minor units remaining of "
                    f"{tree.budget.amount_minor})"
                ),
                on_violation="step_up",
            )
        return CheckResult(
            CHECK_BUDGET, "satisfied", f"{att.amount_minor} within remaining budget"
        )

    def _check_tripwire(self, att: ActionAttestation) -> CheckResult:
        """Deny a high-blast-radius action whose evidence includes untrusted content.

        "What stops a web page from spending my money?" — this, and only for
        high-radius actions. The narrowness is the design: this product does not
        filter what the agent reads, so the agent is expected to consume
        untrusted content constantly. Firing on every action that touched a web
        page would make the tripwire indistinguishable from turning the agent
        off, and customers would disable it.

        Note what this check CANNOT do: it reads the runtime's own claim about
        each piece of evidence. A runtime that mislabels a retrieved page as
        trusted defeats it. That gap is not closeable here — it is audited
        after the fact by the judgement plane's `evidence_mismatch` check, and
        the spec lists it as a standing risk rather than a solved problem.
        """
        try:
            action_class = self.registry.get(att.action)
        except UnknownActionClass as exc:
            return CheckResult(
                check_id=CHECK_INJECTION_TRIPWIRE,
                status="indeterminate",
                detail=str(exc),
                on_violation="deny",
            )
        if action_class.blast_radius != "high":
            return CheckResult(
                CHECK_INJECTION_TRIPWIRE,
                "satisfied",
                f"{att.action} is {action_class.blast_radius} blast radius",
            )
        if att.worst_origin() == "untrusted":
            tainted = [e.locator or e.content_hash[:12] for e in att.evidence if e.origin == "untrusted"]
            return CheckResult(
                check_id=CHECK_INJECTION_TRIPWIRE,
                status="violated",
                detail=(
                    f"high blast-radius action {att.action!r} rests on untrusted "
                    f"content ({', '.join(tainted)}): an instruction the agent read "
                    "cannot authorize an action the user did not"
                ),
                on_violation="deny",
            )
        return CheckResult(
            CHECK_INJECTION_TRIPWIRE, "satisfied", "no untrusted content in the evidence chain"
        )

    # -- resolution -------------------------------------------------------

    @staticmethod
    def _safe_node(tree: TaskTree, node_id: str):
        """The calling node, or None if the tree never minted it.

        Returns None instead of raising so that a bogus node id produces a
        DECISION with reasons — several checks each reporting what they could
        not establish — rather than an exception a gateway has to translate.
        """
        try:
            return tree.node(node_id)
        except Exception:
            return None

    def _resolve(
        self,
        att: ActionAttestation,
        checks: tuple[CheckResult, ...],
        mission: Mission,
        now: int,
    ) -> Decision:
        """Fold the checks into one effect. The ONLY place precedence is defined.

        deny > step_up > allow, with `indeterminate` counting as that check's
        own violation (fail closed). Reasons are the check ids of everything
        that did not pass, in check order, so two decisions on the same inputs
        produce byte-identical reason lists — a property the evidence export
        depends on and which sorting or set-building would quietly break.
        """
        effect: Effect = "allow"
        reasons: list[str] = []
        deltas: list[str] = []

        for check in checks:
            if check.status == "satisfied":
                continue
            reasons.append(check.check_id)
            if check.on_violation == "deny":
                effect = "deny"
            elif effect != "deny":
                effect = "step_up"
                deltas.append(check.detail)

        # A step-up that cannot name what changed is just a second consent
        # screen. If we somehow reached step_up with nothing to show, that is a
        # bug in a check's detail text, not a reason to ask the user anyway.
        if effect == "step_up" and not deltas:
            deltas = [c.detail for c in checks if c.status != "satisfied" and c.detail]

        return Decision(
            effect=effect,
            tree_id=att.tree_id,
            node_id=att.node_id,
            mission_id=mission.mission_id,
            checks=checks,
            reasons=tuple(reasons),
            step_up_delta=tuple(deltas),
            decided_at=now,
            policy_version=self.policy_version,
        )

    def _deny_outright(
        self, att: ActionAttestation, check_id: str, detail: str, now: int
    ) -> Decision:
        """A denial reached before a Mission could be resolved.

        `mission_id` is empty here and that is honest: we are refusing a call
        precisely because we could not find the consent it claims to rest on.
        Inventing a placeholder would put an id in the evidence chain that
        refers to nothing.
        """
        check = CheckResult(check_id=check_id, status="violated", detail=detail, on_violation="deny")
        return Decision(
            effect="deny",
            tree_id=att.tree_id,
            node_id=att.node_id,
            mission_id="",
            checks=(check,),
            reasons=(check_id,),
            decided_at=now,
            policy_version=self.policy_version,
        )
