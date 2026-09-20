"""Step-up: pause one branch, ask a human, resume on approval.

The PDS already returns `step_up` with the delta that caused it. Nothing
carried that to a person, captured their answer, or let the call proceed — so
in practice a step-up was a deny with better wording. This closes the loop.

Five properties, and each is load-bearing.

**An approval covers ONE call.** It is keyed on the exact tree, node, action
and argument hash, and it is consumed when used. Approving a payment to a new
supplier authorizes that payment, not that supplier — otherwise the first
approval in a task would quietly widen the Mission for the rest of it.

**An approval can only lift a step-up, never a deny.** This is the one rule
worth being absolute about. A forged signature, an injected instruction driving
a transfer, a sub-agent reaching past its grant: none of those are things a
tired human at 2am should be able to wave through, and offering the button at
all would eventually get it pressed. The spec scopes step-up to "a denied delta
(new counterparty, over budget)", and that is exactly the set that reaches here.

**Only the person who approved the task may approve a change to it.** With an
IdP configured, the approver presents their own token and the subject must match
the Mission's. The agent cannot mint one, so the agent cannot approve its own
request — which is the whole point of asking.

**Delivery never touches the call path.** A decision must not wait on Slack. The
notification goes out on a background thread, and a dead webhook loses nothing:
the approval is already stored and can be listed, polled or approved directly.
A channel that fails is recorded on the approval rather than retried forever.

**An unanswered approval expires.** A pending request that lived forever would
turn into a permission somebody granted months ago and forgot.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Literal, Optional

Status = Literal["pending", "approved", "denied", "expired", "consumed"]


class ApprovalError(Exception):
    """An approval could not be created, decided or used."""


@dataclass(frozen=True)
class Approval:
    """One paused branch, waiting on one person."""

    approval_id: str
    tree_id: str
    node_id: str
    mission_id: str
    # Who must answer. The Mission's subject — the person whose instruction
    # this task is executing, not whoever happens to be on call.
    approver: str
    action: str
    # What makes this approval cover exactly one call and no other.
    args_hash: str
    # What the operator is being shown, in the check's own words.
    delta: tuple[str, ...]
    reasons: tuple[str, ...]
    counterparty: str = ""
    amount_minor: int = 0
    currency: str = ""
    instruction_hash: str = ""
    requested_at: int = 0
    expires_at: int = 0
    status: Status = "pending"
    decided_by: str = ""
    # HOW the answer arrived: a verified IdP subject, or a service credential
    # standing in for one where no IdP is configured. Recorded separately from
    # `decided_by` so the audit trail never loses the difference between "the
    # operator approved this" and "a component approved it on their behalf".
    decided_via: str = ""
    decided_at: int = 0
    note: str = ""
    delivery: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, str, str]:
        """What a retried call must match to be covered by this approval."""
        return (self.tree_id, self.node_id, self.action, self.args_hash)

    def to_wire(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "status": self.status,
            "tree_id": self.tree_id,
            "node_id": self.node_id,
            "mission_id": self.mission_id,
            "approver": self.approver,
            "action": self.action,
            "args_hash": self.args_hash,
            "delta": list(self.delta),
            "reasons": list(self.reasons),
            "counterparty": self.counterparty,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "decided_by": self.decided_by,
            "decided_via": self.decided_via,
            "decided_at": self.decided_at,
            "note": self.note,
            "delivery": list(self.delivery),
        }


# --- delivery ---------------------------------------------------------------


class Delivery:
    """Somewhere a person will see a pending approval."""

    name = "none"

    def send(self, approval: Approval, link: str) -> str:
        """Return a short note about what happened, for the audit record."""
        raise NotImplementedError


def summarize(approval: Approval, link: str) -> str:
    """The message a person reads.

    Written to be understood by someone who was not watching the agent work:
    what it wants to do, what changed, and under whose instruction. A
    notification that says "approval required for tree-7f3a node 2" trains
    people to approve without reading.
    """
    what = approval.action
    if approval.counterparty:
        what += f" to {approval.counterparty}"
    if approval.amount_minor:
        what += f" for {approval.amount_minor / 100:,.2f} {approval.currency}".rstrip()
    lines = [
        f"An agent is waiting on you: {what}.",
        "",
        "What changed from what you approved:",
    ]
    lines += [f"  • {d}" for d in (approval.delta or ("(no detail recorded)",))]
    if link:
        lines += ["", f"Approve or decline: {link}"]
    return "\n".join(lines)


class LogDelivery(Delivery):
    """Prints to the service log. Always available, and honest about it.

    Not a placeholder to be embarrassed about: a deployment piping logs into
    its own alerting already has a delivery channel, and one that works when
    the webhook is down.
    """

    name = "log"

    def __init__(self, write: Callable[[str], None] = print) -> None:
        self._write = write

    def send(self, approval: Approval, link: str) -> str:
        self._write(
            f"\n=== APPROVAL REQUIRED ({approval.approval_id}) ===\n"
            f"{summarize(approval, link)}\n"
            f"for: {approval.approver}\n"
        )
        return "log"


class WebhookDelivery(Delivery):
    """POSTs to a URL. Covers Slack and Teams incoming webhooks as they are.

    One generic channel rather than a vendor SDK per destination: Slack and
    Teams both accept a JSON body with a `text` field, and a deployment that
    needs something richer has a URL it can point at its own adapter. Adding
    three SDKs to the enforcement plane to format three messages would be a
    poor trade for a component that must stay small and auditable.
    """

    name = "webhook"

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    def send(self, approval: Approval, link: str) -> str:
        body = json.dumps({
            "text": summarize(approval, link),
            "approval_id": approval.approval_id,
            "approver": approval.approver,
            "link": link,
        }).encode()
        request = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return f"webhook:{response.status}"
        except urllib.error.HTTPError as exc:
            return f"webhook:{exc.code}"
        except Exception as exc:
            # Recorded, not raised. A failed notification must not lose an
            # approval that is already stored, and must never fail the decision
            # that created it — which happened on another thread anyway.
            return f"webhook:failed:{type(exc).__name__}"


# --- store ------------------------------------------------------------------


class ApprovalService:
    """Creates, delivers, decides and consumes approvals."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 900,
        channels: Iterable[Delivery] = (),
        link_base: str = "",
        deliver_async: bool = True,
    ) -> None:
        self.ttl = ttl_seconds
        self.channels = list(channels)
        self.link_base = link_base.rstrip("/")
        self.deliver_async = deliver_async
        self._lock = threading.RLock()
        self._by_id: dict[str, Approval] = {}
        # Only PENDING and APPROVED entries are indexed by call key; a consumed
        # or denied one must not be findable as cover for a retry.
        self._by_key: dict[tuple[str, str, str, str], str] = {}

    # -- lifecycle --------------------------------------------------------

    def _expire_locked(self, now: int) -> None:
        for approval_id, approval in list(self._by_id.items()):
            if approval.status in ("pending", "approved") and 0 < approval.expires_at <= now:
                self._by_id[approval_id] = replace(approval, status="expired")
                self._by_key.pop(approval.key, None)

    def find_for_call(self, tree_id: str, node_id: str, action: str, args_hash: str,
                      now: Optional[int] = None) -> Optional[Approval]:
        now = int(time.time()) if now is None else now
        with self._lock:
            self._expire_locked(now)
            approval_id = self._by_key.get((tree_id, node_id, action, args_hash))
            return self._by_id.get(approval_id) if approval_id else None

    def request(
        self,
        *,
        tree_id: str,
        node_id: str,
        mission_id: str,
        approver: str,
        action: str,
        args_hash: str,
        delta: Iterable[str],
        reasons: Iterable[str],
        counterparty: str = "",
        amount_minor: int = 0,
        currency: str = "",
        now: Optional[int] = None,
    ) -> Approval:
        """Create a pending approval, or return the one already waiting.

        Idempotent on the call key. An agent that retries while a human is
        still deciding must not produce a second notification — that is how an
        approver ends up with forty messages for one payment and stops reading
        them.
        """
        now = int(time.time()) if now is None else now
        key = (tree_id, node_id, action, args_hash)
        with self._lock:
            self._expire_locked(now)
            existing_id = self._by_key.get(key)
            if existing_id:
                existing = self._by_id[existing_id]
                if existing.status in ("pending", "approved"):
                    return existing

            approval = Approval(
                approval_id=uuid.uuid4().hex,
                tree_id=tree_id, node_id=node_id, mission_id=mission_id,
                approver=approver, action=action, args_hash=args_hash,
                delta=tuple(delta), reasons=tuple(reasons),
                counterparty=counterparty, amount_minor=amount_minor, currency=currency,
                requested_at=now, expires_at=now + self.ttl, status="pending",
            )
            self._by_id[approval.approval_id] = approval
            self._by_key[key] = approval.approval_id

        self._deliver(approval)
        return self._by_id[approval.approval_id]

    def _deliver(self, approval: Approval) -> None:
        """Notify, off the call path.

        A decision must not wait on Slack, so this runs on its own thread
        unless a caller (a test) asks for it inline. Results are folded back
        onto the approval record so an operator can see whether the message
        actually went anywhere.
        """
        if not self.channels:
            return

        link = f"{self.link_base}/v1/approvals/{approval.approval_id}" if self.link_base else ""

        def run() -> None:
            notes = []
            for channel in self.channels:
                try:
                    notes.append(channel.send(approval, link))
                except Exception as exc:  # a channel must never break the service
                    notes.append(f"{channel.name}:failed:{type(exc).__name__}")
            with self._lock:
                current = self._by_id.get(approval.approval_id)
                if current is not None:
                    self._by_id[approval.approval_id] = replace(
                        current, delivery=tuple(notes)
                    )

        if self.deliver_async:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()

    def decide(self, approval_id: str, *, approved: bool, by: str, via: str = "",
               note: str = "", now: Optional[int] = None) -> Approval:
        """Record a human's answer.

        `by` must be the approval's own approver — that invariant stays
        absolute here regardless of how the caller established it. `via` says
        how: a verified IdP subject, or a credential authorized to stand in.
        """
        now = int(time.time()) if now is None else now
        with self._lock:
            self._expire_locked(now)
            approval = self._by_id.get(approval_id)
            if approval is None:
                raise ApprovalError(f"unknown approval {approval_id!r}")
            if approval.status == "expired":
                raise ApprovalError(
                    f"approval {approval_id!r} expired at {approval.expires_at}. "
                    "The agent must ask again, so the operator sees a current request"
                )
            if approval.status != "pending":
                raise ApprovalError(
                    f"approval {approval_id!r} was already {approval.status}"
                    + (f" by {approval.decided_by}" if approval.decided_by else "")
                )
            if by != approval.approver:
                # Checked here as well as at the route, because this is the
                # invariant — only the person whose instruction the task is
                # executing may change what it may do.
                raise ApprovalError(
                    f"{by!r} is not the approver for this request; it belongs to "
                    f"{approval.approver!r}"
                )
            updated = replace(
                approval,
                status="approved" if approved else "denied",
                decided_by=by, decided_via=via or by, decided_at=now, note=note,
            )
            self._by_id[approval_id] = updated
            if not approved:
                self._by_key.pop(approval.key, None)
            return updated

    def consume(self, approval: Approval, now: Optional[int] = None) -> Approval:
        """Spend an approval on the call it covers. Single use.

        Called only after the decision it unblocks has been made, so a call
        that is still denied for another reason does not burn the approval.
        """
        now = int(time.time()) if now is None else now
        with self._lock:
            current = self._by_id.get(approval.approval_id)
            if current is None or current.status != "approved":
                raise ApprovalError(
                    f"approval {approval.approval_id!r} is not available to use"
                )
            spent = replace(current, status="consumed")
            self._by_id[approval.approval_id] = spent
            self._by_key.pop(current.key, None)
            return spent

    # -- reads ------------------------------------------------------------

    def get(self, approval_id: str, now: Optional[int] = None) -> Approval:
        now = int(time.time()) if now is None else now
        with self._lock:
            self._expire_locked(now)
            approval = self._by_id.get(approval_id)
        if approval is None:
            raise ApprovalError(f"unknown approval {approval_id!r}")
        return approval

    def list(self, *, status: str = "", tree_id: str = "", approver: str = "",
             now: Optional[int] = None) -> list[Approval]:
        now = int(time.time()) if now is None else now
        with self._lock:
            self._expire_locked(now)
            items = list(self._by_id.values())
        return sorted(
            (a for a in items
             if (not status or a.status == status)
             and (not tree_id or a.tree_id == tree_id)
             and (not approver or a.approver == approver)),
            key=lambda a: a.requested_at,
            reverse=True,
        )
