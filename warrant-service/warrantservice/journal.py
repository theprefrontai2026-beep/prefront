"""The decision journal: what this deployment actually decided, and why.

The PDS decided and forgot. That is defensible for a component on a 2ms
budget, and useless for everyone else — an operator asking "what has this agent
been doing?", a reviewer asking "why was that payment stopped?", an auditor
asking for the evidence behind a control. The spec's Prove stage promises
evidence packs are "generated, not assembled", which is only possible if the
decisions were kept in the first place.

Three constraints shape it.

**It must not slow a decision.** An append to a bounded deque under a lock is
the whole cost. No serialization, no I/O, no formatting — records are stored as
they are and rendered when read, because reads are rare and decisions are not.

**It is bounded, and drops the OLDEST.** Memory here is a fixed cost rather
than a leak that takes the enforcement plane down at 3am. Keeping the newest is
the right end to keep: an operator investigating works backwards from now, and
a full history belongs in the SIEM export the spec describes, not in the
decision service's RAM.

**It records the decision, never the payload.** Arguments are already reduced
to a hash by the time they reach the PDS, and nothing here reaches for more.
The control zone holds hashes; payloads stay in the tenant's evidence store.
That split is the reason a bank can run this at all.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class Entry:
    """One decision, with enough context to be understood without a join."""

    seq: int
    at: int
    effect: str
    reasons: tuple[str, ...]
    tree_id: str
    node_id: str
    mission_id: str
    subject: str
    action: str
    resource: str
    counterparty: str
    amount_minor: int
    actor_chain: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]
    policy_version: str
    approval_id: str = ""
    # Worst provenance class anywhere in the evidence chain. Kept because it is
    # the one thing that explains an injection-tripwire denial to a reader who
    # was not there, and it costs a word.
    worst_origin: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "at": self.at, "effect": self.effect,
            "reasons": list(self.reasons), "tree_id": self.tree_id,
            "node_id": self.node_id, "mission_id": self.mission_id,
            "subject": self.subject, "action": self.action,
            "resource": self.resource, "counterparty": self.counterparty,
            "amount_minor": self.amount_minor,
            "actor_chain": list(self.actor_chain),
            "checks": [dict(c) for c in self.checks],
            "policy_version": self.policy_version,
            "approval_id": self.approval_id,
            "worst_origin": self.worst_origin,
        }


class Journal:
    """A bounded, newest-last record of decisions."""

    def __init__(self, capacity: int = 1000) -> None:
        self._entries: deque[Entry] = deque(maxlen=max(1, capacity))
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    @property
    def capacity(self) -> int:
        return self._entries.maxlen or 0

    def record(self, *, decision, attestation, subject: str, actor_chain=(),
               approval_id: str = "", at: Optional[int] = None) -> Entry:
        entry = Entry(
            seq=next(self._seq),
            at=int(time.time()) if at is None else at,
            effect=decision.effect,
            reasons=tuple(decision.reasons),
            tree_id=decision.tree_id,
            node_id=decision.node_id,
            mission_id=decision.mission_id,
            subject=subject,
            action=attestation.action,
            resource=attestation.resource,
            counterparty=attestation.counterparty,
            amount_minor=attestation.amount_minor,
            actor_chain=tuple(actor_chain),
            checks=tuple(
                {"check_id": c.check_id, "status": c.status,
                 "detail": c.detail, "on_violation": c.on_violation}
                for c in decision.checks
            ),
            policy_version=decision.policy_version,
            approval_id=approval_id,
            worst_origin=attestation.worst_origin() if attestation.evidence else "",
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    # -- reads ------------------------------------------------------------

    def query(
        self,
        *,
        effect: str = "",
        action: str = "",
        tree_id: str = "",
        subject: str = "",
        reason: str = "",
        since: int = 0,
        limit: int = 100,
    ) -> list[Entry]:
        """Newest first, because that is how anyone investigating reads."""
        with self._lock:
            items = list(self._entries)
        out = [
            e for e in reversed(items)
            if (not effect or e.effect == effect)
            and (not action or e.action == action)
            and (not tree_id or e.tree_id == tree_id)
            and (not subject or e.subject == subject)
            and (not reason or reason in e.reasons)
            and (not since or e.at >= since)
        ]
        return out[: max(1, min(limit, 1000))]

    def stats(self) -> dict[str, Any]:
        """Aggregates an operator actually asks for.

        Computed on read rather than maintained incrementally: the journal is
        small and bounded, reads are rare, and a counter kept in parallel with
        a bounded buffer drifts the moment an entry is evicted.
        """
        with self._lock:
            items = list(self._entries)

        by_effect: dict[str, int] = {"allow": 0, "deny": 0, "step_up": 0}
        by_reason: dict[str, int] = {}
        by_action: dict[str, int] = {}
        stopped_minor = 0
        held_minor = 0

        for e in items:
            by_effect[e.effect] = by_effect.get(e.effect, 0) + 1
            by_action[e.action] = by_action.get(e.action, 0) + 1
            for reason in e.reasons:
                by_reason[reason] = by_reason.get(reason, 0) + 1
            if e.effect == "deny":
                stopped_minor += e.amount_minor
            elif e.effect == "step_up":
                held_minor += e.amount_minor

        return {
            "total": len(items),
            "capacity": self.capacity,
            "by_effect": by_effect,
            "top_reasons": sorted(by_reason.items(), key=lambda kv: -kv[1])[:8],
            "by_action": sorted(by_action.items(), key=lambda kv: -kv[1]),
            "value_stopped_minor": stopped_minor,
            "value_held_minor": held_minor,
            "first_at": items[0].at if items else 0,
            "last_at": items[-1].at if items else 0,
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
