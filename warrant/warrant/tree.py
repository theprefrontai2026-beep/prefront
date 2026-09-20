"""The task tree: nodes, the narrowing grant, the shared budget ledger, revocation.

This is the Task Control Plane's system of record, and it is what makes the
spec's central claim — "one task reads as one story from consent to outcome" —
mechanically true rather than aspirational. Every token, decision, payload and
verdict in the system carries the `tree_id` and `node_id` minted here.

Three invariants live in this module, and each answers a buyer question the
spec poses directly.

**Narrowing is one-way ("can a sub-agent escalate?").** A child node's grant
must be a SUBSET of its parent's. `Grant.narrow` cannot widen even when asked
to — handing it an action class the parent lacks drops that class rather than
adding it. This is the one place where a permissive implementation would undo
the entire product, so it is written as an intersection, which has no failure
mode, rather than as a check that could be forgotten at one call site.

**Depth is capped ("how far can this go?").** The Mission states `max_depth`;
a spawn past it raises. An uncapped tree is how one delegation becomes a
thousand.

**The budget is the TREE's, not the node's ("can a leaked token drain me?").**
Sub-agents share one ledger, so fanning out does not multiply the ceiling.
Per-user and per-agent aggregate budgets are explicitly Phase 3 in the spec and
are not modelled here — the ledger is keyed on the tree and nothing else.

Spending is RESERVE-then-SETTLE, not charge-on-allow. Two calls deciding
concurrently would each see the full remaining budget and each be allowed, so
a ceiling enforced only at decision time is not a ceiling. The reservation is
taken by the caller that is about to execute, and released if the call never
happened. The cost of getting this wrong is money, which is the one resource a
customer will not accept an approximate answer about.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

from .contract import Budget, ContractError, Mission


class TreeError(Exception):
    """A task-tree operation was refused."""


class BudgetExceeded(TreeError):
    """A reservation would take the tree past its ceiling.

    Its own type because the caller's response differs: an over-budget call is
    a candidate for a step-up (show the user the delta), while every other
    `TreeError` is a bug or an attack.
    """


@dataclass(frozen=True)
class Grant:
    """The effective permission at one node: what this actor may do, here.

    Starts as the Mission's own boundary at the root and can only shrink on the
    way down. An empty tuple means UNCONSTRAINED on that axis, matching
    `Mission`'s convention — the two must agree, since the root grant is built
    directly from a Mission.
    """

    action_classes: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    counterparties: tuple[str, ...] = ()

    @classmethod
    def from_mission(cls, mission: Mission) -> "Grant":
        return cls(
            action_classes=tuple(mission.action_classes),
            resources=tuple(mission.resources),
            counterparties=tuple(mission.counterparties),
        )

    @staticmethod
    def _narrow_axis(parent: tuple[str, ...], requested: tuple[str, ...]) -> tuple[str, ...]:
        """Intersect one axis, honouring the empty-means-unconstrained rule.

        The asymmetry is deliberate and is the whole invariant:

          parent empty, requested set   -> requested  (a narrowing; allowed)
          parent set,   requested empty -> parent     (asking for "everything"
                                                       gets you the parent's
                                                       everything, not the
                                                       world's)
          both set                      -> intersection

        The second line is the one that matters. A sub-agent that requests an
        unconstrained grant is not asking to escalate, it is asking to inherit —
        and inheriting the parent's list is the only reading that cannot widen.
        """
        if not parent:
            return tuple(requested)
        if not requested:
            return tuple(parent)
        allowed = set(parent)
        # Preserve the REQUESTED order rather than sorting: the order a caller
        # asked in is the order a reviewer reading the consent trail expects,
        # and sorting would make two equivalent grants look different in a diff.
        return tuple(item for item in requested if item in allowed)

    def narrow(self, requested: "Grant") -> "Grant":
        """The child grant. Cannot widen, whatever `requested` contains."""
        return Grant(
            action_classes=self._narrow_axis(self.action_classes, requested.action_classes),
            resources=self._narrow_axis(self.resources, requested.resources),
            counterparties=self._narrow_axis(self.counterparties, requested.counterparties),
        )

    def permits_action(self, action_class: str) -> bool:
        return not self.action_classes or action_class in self.action_classes

    def permits_resource(self, resource: str) -> bool:
        return not self.resources or resource in self.resources

    def permits_counterparty(self, counterparty: str) -> bool:
        return not self.counterparties or counterparty in self.counterparties


@dataclass(frozen=True)
class TaskNode:
    """One actor's place in the tree.

    `actor_chain` is stored in full rather than walked from `parent_id` on
    demand. The spec requires the chain on every token and in every record, and
    an incident review reads thousands of records against a tree whose
    intermediate nodes may have been pruned by retention — a chain that has to
    be reconstructed is a chain that can fail to reconstruct.
    """

    node_id: str
    tree_id: str
    actor: str
    grant: Grant
    depth: int = 0
    parent_id: str = ""
    actor_chain: tuple[str, ...] = ()
    created_at: int = 0


class TaskTree:
    """One task: its nodes, its shared ledger, its liveness.

    Mutable and lock-guarded, unlike everything in `contract.py`. This is the
    system of record — the thing decisions are checked against — and it changes
    as the task runs. The lock is not decorative: `reserve` is a
    check-then-act, and the whole point of reserving is that two branches can
    reach it at the same instant.
    """

    def __init__(self, tree_id: str, mission: Mission, root_actor: str, created_at: int = 0) -> None:
        if not tree_id:
            raise TreeError("a task tree must have a tree_id")
        if not root_actor:
            raise TreeError("a task tree must name its root actor")
        self.tree_id = tree_id
        self.mission = mission
        self.budget: Budget = mission.budget
        self.created_at = created_at

        self._lock = threading.RLock()
        self._nodes: dict[str, TaskNode] = {}
        self._reserved_minor = 0
        self._settled_minor = 0
        # handle -> amount held. Per instance, never class-level: a shared
        # dict would make one task's reservations visible to another.
        self._holds: dict[str, int] = {}
        self._calls = 0
        self._revoked_at: Optional[int] = None
        self._revoked_reason = ""

        root = TaskNode(
            node_id=f"{tree_id}:0",
            tree_id=tree_id,
            actor=root_actor,
            grant=Grant.from_mission(mission),
            depth=0,
            parent_id="",
            actor_chain=(root_actor,),
            created_at=created_at,
        )
        self._nodes[root.node_id] = root
        self.root_id = root.node_id

    # -- structure --------------------------------------------------------

    def node(self, node_id: str) -> TaskNode:
        with self._lock:
            node = self._nodes.get(node_id)
        if node is None:
            raise TreeError(
                f"unknown node {node_id!r} in tree {self.tree_id!r}: a call "
                "naming a node this tree never minted is not a call this tree "
                "can vouch for"
            )
        return node

    def nodes(self) -> tuple[TaskNode, ...]:
        with self._lock:
            return tuple(self._nodes.values())

    def spawn(
        self,
        parent_id: str,
        actor: str,
        requested: Optional[Grant] = None,
        created_at: int = 0,
    ) -> TaskNode:
        """Mint a child node — the ONLY way a sub-agent gets a token.

        "Sub-agents get narrower tokens by exchange only": there is no
        constructor that produces a node without a parent, other than the root
        the Mission itself authorizes.
        """
        with self._lock:
            if self._revoked_at is not None:
                raise TreeError(
                    f"tree {self.tree_id!r} was revoked at {self._revoked_at}"
                    f"{f' ({self._revoked_reason})' if self._revoked_reason else ''}: "
                    "a revoked tree cannot grow"
                )
            parent = self._nodes.get(parent_id)
            if parent is None:
                raise TreeError(f"unknown parent node {parent_id!r} in tree {self.tree_id!r}")

            depth = parent.depth + 1
            if depth > self.mission.max_depth:
                raise TreeError(
                    f"sub-agent depth {depth} exceeds the Mission's max_depth of "
                    f"{self.mission.max_depth}: the user approved a task "
                    f"{self.mission.max_depth} level(s) deep, and delegation is "
                    "not implied by the power to delegate once"
                )

            child = TaskNode(
                node_id=f"{self.tree_id}:{len(self._nodes)}",
                tree_id=self.tree_id,
                actor=actor,
                grant=parent.grant.narrow(requested or Grant()),
                depth=depth,
                parent_id=parent.node_id,
                actor_chain=parent.actor_chain + (actor,),
                created_at=created_at,
            )
            self._nodes[child.node_id] = child
            return child

    # -- liveness ---------------------------------------------------------

    @property
    def revoked(self) -> bool:
        with self._lock:
            return self._revoked_at is not None

    @property
    def revoked_reason(self) -> str:
        with self._lock:
            return self._revoked_reason

    def revoke(self, at: int, reason: str = "") -> None:
        """Stop the whole task. Idempotent, and the FIRST reason wins.

        Idempotent because "one call revokes every token in a task" will in
        practice be made by a human hitting a button and by an anomaly signal
        at nearly the same moment, and the second must not fail. First-reason-
        wins because the earliest cause is the one an incident review wants;
        a later "revoked by cleanup" would overwrite "spend velocity anomaly".
        """
        with self._lock:
            if self._revoked_at is None:
                self._revoked_at = at
                self._revoked_reason = reason

    def is_live_at(self, now: int) -> bool:
        """Liveness is revocation AND the Mission's window, together.

        Kept in one place so no caller can check one and forget the other — an
        expired Mission whose tree was never revoked is exactly as dead as a
        revoked one, and the two failures look identical to a user.
        """
        return not self.revoked and self.mission.is_live_at(now)

    # -- budget ledger ----------------------------------------------------

    @property
    def spend_committed(self) -> int:
        """Settled spend only — what actually moved."""
        with self._lock:
            return self._settled_minor

    @property
    def spend_outstanding(self) -> int:
        """Reserved but not yet settled — in flight."""
        with self._lock:
            return self._reserved_minor

    @property
    def spend_total(self) -> int:
        """What the ceiling is measured against: settled plus in-flight.

        Using this rather than settled-only is the difference between a ceiling
        and a suggestion, because a concurrent branch's reservation has to
        count against me before it settles or we both pass the same check.
        """
        with self._lock:
            return self._settled_minor + self._reserved_minor

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._calls

    def remaining_minor(self) -> Optional[int]:
        """Headroom, or None when the budget does not cap spend at all."""
        if not self.budget.caps_spend:
            return None
        with self._lock:
            return self.budget.amount_minor - (self._settled_minor + self._reserved_minor)

    def would_exceed(self, amount_minor: int) -> bool:
        """Pure check — no mutation, no lock held across a caller's work.

        This is what the PDS calls. The PDS is a pure function of its inputs
        (so a proposed policy can be replayed against last week's recorded
        attestations, which the spec's sandbox requires), so it must be able to
        ask the budget question without taking a reservation.
        """
        remaining = self.remaining_minor()
        if remaining is None:
            return False
        return amount_minor > remaining

    def calls_exhausted(self) -> bool:
        if not self.budget.caps_calls:
            return False
        with self._lock:
            return self._calls >= self.budget.max_calls

    def reserve(self, amount_minor: int, at: int = 0) -> str:
        """Hold budget for a call that is about to happen. Returns a handle.

        Increments the call counter too, so a reservation is the single place
        the tree learns a call occurred — a counter bumped somewhere else would
        drift from the ledger the moment one path forgot.
        """
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
            raise TreeError("reservation amount must be an integer in minor units")
        if amount_minor < 0:
            raise TreeError("cannot reserve a negative amount")
        with self._lock:
            if self._revoked_at is not None:
                raise TreeError(f"tree {self.tree_id!r} is revoked; no further spend")
            if self.budget.caps_calls and self._calls >= self.budget.max_calls:
                raise BudgetExceeded(
                    f"tree {self.tree_id!r} has used its {self.budget.max_calls} "
                    "permitted calls"
                )
            if self.budget.caps_spend:
                projected = self._settled_minor + self._reserved_minor + amount_minor
                if projected > self.budget.amount_minor:
                    raise BudgetExceeded(
                        f"tree {self.tree_id!r} would spend {projected} "
                        f"{self.budget.currency} minor units against a ceiling of "
                        f"{self.budget.amount_minor}"
                    )
            self._reserved_minor += amount_minor
            self._calls += 1
            handle = f"{self.tree_id}:res:{self._calls}"
            self._holds[handle] = amount_minor
            return handle

    def settle(self, handle: str, actual_minor: Optional[int] = None) -> None:
        """The call happened. Convert a reservation into committed spend.

        `actual_minor` exists because the amount attested is an intent and the
        amount moved is a fact, and they differ legitimately (a partial fill, a
        fee). Settling MORE than was reserved is refused rather than absorbed:
        that is precisely the shape of an over-spend sneaking past a ceiling
        that was checked against a smaller number.
        """
        with self._lock:
            held = self._holds.pop(handle, None)
            if held is None:
                raise TreeError(
                    f"unknown or already-settled reservation {handle!r}: settling "
                    "twice would double-count the spend"
                )
            amount = held if actual_minor is None else actual_minor
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise TreeError("settled amount must be a non-negative integer")
            if amount > held:
                # Put it back before raising, so a rejected settle does not also
                # silently release the hold and let the next call through.
                self._holds[handle] = held
                raise TreeError(
                    f"settled {amount} against a reservation of {held}: a call "
                    "cannot spend more than it reserved, or the ceiling was "
                    "never checked against what actually moved"
                )
            self._reserved_minor -= held
            self._settled_minor += amount

    def release(self, handle: str) -> None:
        """The call did not happen (denied downstream, transport failure).

        Releasing a reservation does NOT decrement the call counter. The call
        was attempted, and `max_calls` bounds attempts — otherwise a failing
        agent could retry forever for free, which is the "retry storm" the
        judgement plane has a whole check for.
        """
        with self._lock:
            held = self._holds.pop(handle, None)
            if held is None:
                raise TreeError(f"unknown or already-resolved reservation {handle!r}")
            self._reserved_minor -= held


class TreeStore:
    """In-memory system of record for live trees, plus the revocation denylist.

    The spec requires a revocation that "reaches every PDS and gateway within
    5 s even when an issuer does not cooperate". Distribution is not this
    class's job — this is the local view each PDS holds. What it does provide
    is the shape that distribution needs: revocation is recorded as a
    TREE ID plus a timestamp, so it can be replicated as a small append-only
    set rather than by re-synchronising whole trees.

    The denylist deliberately outlives the tree. A PDS that has evicted a
    finished tree must still refuse its tokens, or revocation would expire
    into permission.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trees: dict[str, TaskTree] = {}
        self._denied: dict[str, int] = {}

    def create(self, tree_id: str, mission: Mission, root_actor: str, created_at: int = 0) -> TaskTree:
        with self._lock:
            if tree_id in self._trees:
                raise TreeError(f"tree {tree_id!r} already exists")
            if tree_id in self._denied:
                raise TreeError(
                    f"tree {tree_id!r} is on the revocation denylist: a revoked "
                    "tree id is never reused, or revocation could be undone by "
                    "starting the task again under the same id"
                )
            tree = TaskTree(tree_id, mission, root_actor, created_at=created_at)
            self._trees[tree_id] = tree
            return tree

    def get(self, tree_id: str) -> TaskTree:
        with self._lock:
            tree = self._trees.get(tree_id)
        if tree is None:
            raise TreeError(f"unknown tree {tree_id!r}")
        return tree

    def tree_ids(self) -> tuple[str, ...]:
        """Every tree this store holds, in creation order.

        Does NOT include denylisted trees this replica never saw — those are
        revocations, not tasks, and `denylist()` is where they live. Conflating
        them would make an operations view show tasks that never ran here.
        """
        with self._lock:
            return tuple(self._trees)

    def revoke(self, tree_id: str, at: int, reason: str = "") -> None:
        """Revoke a tree, whether or not this node has ever seen it.

        Accepting an unknown tree id is the point: the denylist has to work
        "even when an issuer does not cooperate", which means a PDS must be
        able to act on a revocation for a tree whose creation it never
        observed.
        """
        with self._lock:
            self._denied.setdefault(tree_id, at)
            tree = self._trees.get(tree_id)
        if tree is not None:
            tree.revoke(at, reason)

    def is_denied(self, tree_id: str) -> bool:
        with self._lock:
            return tree_id in self._denied

    def denylist(self) -> dict[str, int]:
        """The replicable set: tree id -> revocation time."""
        with self._lock:
            return dict(self._denied)

    def merge_denylist(self, entries: dict[str, int]) -> int:
        """Fold in a peer's denylist. Returns how many were new.

        Merge rather than replace, and keep the EARLIEST timestamp per id: two
        PDS replicas may learn of a revocation from different sources, and the
        earliest is the one that bounds the exposure window in an incident
        report.
        """
        added = 0
        with self._lock:
            for tree_id, at in entries.items():
                existing = self._denied.get(tree_id)
                if existing is None:
                    self._denied[tree_id] = at
                    added += 1
                elif at < existing:
                    self._denied[tree_id] = at
                tree = self._trees.get(tree_id)
                if tree is not None:
                    tree.revoke(at, "revoked via denylist merge")
        return added
