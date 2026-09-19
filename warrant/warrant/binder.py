"""Intent Binder: sign one Action Attestation per side-effect call.

This is the only component that runs inside the customer's agent process, and
the spec is blunt about why it has to: that process is the "only place the user
instruction, plan and retrieved content exist". Nothing downstream can
reconstruct which retrieved document a call rested on, because by the time the
call reaches a gateway it is just arguments.

The Binder's signature therefore proves exactly one thing — which agent process
made this claim — and the rest of the enforcement plane is built on the
assumption that it proves nothing more. It is worth being explicit that the
Binder is INSIDE the blast radius: an attacker who controls the agent controls
this key. That is not a flaw to be fixed here, it is the reason the PDS exists
as a separate party and the reason the judgement plane recomputes provenance
from shipped content rather than trusting `origin` labels.

What the Binder does buy, even fully compromised, is attribution: every claim is
bound to a key, a tree and a node, so an incident review can say which process
said what and when, rather than inferring it from timing.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .canonical import args_hash, content_hash
from .contract import ActionAttestation, EvidenceRef, Origin, SignedAttestation
from .signing import SigningKey


class IntentBinder:
    """Signs attestations for one agent process."""

    def __init__(self, key: SigningKey, tree_id: str, node_id: str) -> None:
        self._key = key
        self.tree_id = tree_id
        self.node_id = node_id

    def evidence(self, content: str, origin: Origin, locator: str = "") -> EvidenceRef:
        """Hash one piece of content the model acted on.

        The Binder hashes; the RUNTIME separately ships the content itself to
        the evidence store. Both are needed and they are not redundant: the
        hash is what the enforcement plane can check in constant time on the
        path, and the content is what the judgement plane needs to compute
        provenance independently of anything claimed here.
        """
        return EvidenceRef(content_hash=content_hash(content), origin=origin, locator=locator)

    def attest(
        self,
        *,
        action: str,
        args: dict[str, Any],
        plan_step: str = "",
        resource: str = "",
        counterparty: str = "",
        amount_minor: int = 0,
        evidence: Iterable[EvidenceRef] = (),
        issued_at: int = 0,
    ) -> SignedAttestation:
        """Build and sign the attestation for one call.

        Takes the ARGUMENTS and hashes them here. A caller that could pass a
        hash directly could attest to one argument bag and send another, which
        is the exact substitution `pds._check_argument_integrity` exists to
        catch — no reason to leave the door open on this side too.
        """
        attestation = ActionAttestation(
            tree_id=self.tree_id,
            node_id=self.node_id,
            action=action,
            args_hash=args_hash(args),
            plan_step=plan_step,
            resource=resource,
            counterparty=counterparty,
            amount_minor=amount_minor,
            evidence=tuple(evidence),
            issued_at=issued_at,
        )
        return SignedAttestation(
            attestation=attestation,
            key_id=self._key.key_id,
            signature=self._key.sign(attestation.to_payload()),
        )

    def for_node(self, node_id: str) -> "IntentBinder":
        """A Binder for a sub-agent node, sharing this process's key.

        Same key, different node: a sub-agent running in the same process is
        the same principal making claims about a different place in the tree.
        A sub-agent in its OWN process gets its own key and its own Binder.
        """
        return IntentBinder(self._key, self.tree_id, node_id)
