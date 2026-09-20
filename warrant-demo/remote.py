"""Drive the same demo against a PDS running as a separate service.

The demo has two modes and the scenarios cannot tell them apart:

  EMBEDDED  `deployment.build()` — the engine as a library, in this process.
  REMOTE    `remote.build(url)`  — every decision over HTTP to warrant-service.

That the twelve situations produce identical results either way is the point,
and `warrant-service/tests/test_demo_parity.py` asserts it. A service interface
that quietly decided differently from the library would be the worst kind of
bug in this system: invisible until an audit, and wrong in the direction of
permission.

What this file is, precisely, is an ADAPTER — it presents the same handful of
attributes `agent.run_governed` reaches for, backed by HTTP calls instead of
objects. It does not reimplement any decision logic, and it could not: the
service holds the tree, the Mission and the keys, and this side holds only the
agent's signing key, which is exactly the split a real deployment has.
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from warrant import Grant, IntentBinder, SigningKey, digest
from warrantservice import RemotePolicyDecisionService

import deployment
from world import ACTIONS, APPROVED_COUNTERPARTIES, CURRENCY, INVOICES

# One id per PROCESS, prefixed onto every Mission and tree this run creates.
#
# The service outlives the console — that is the point of running them as
# separate containers — and the engine refuses to reuse either id: two consents
# sharing a Mission id would be indistinguishable in the evidence chain, and a
# reused tree id would let a revoked task be restarted under its own name.
# Both refusals are correct, so the demo supplies fresh ids rather than asking
# the engine to be lenient. A real deployment does the same thing: each
# night's batch is a new task, not the same one run again.
RUN_ID = uuid.uuid4().hex[:8]


def run_scoped(name: str) -> str:
    """Namespace an id to this process run."""
    return f"{RUN_ID}-{name}"


@dataclass(frozen=True)
class RemoteNode:
    """What `TaskTree.spawn` returns, as far as the scenarios need."""

    node_id: str


class NodeAwarePDS:
    """Routes each decision to the client holding that node's task token.

    In token mode a sub-agent is a different principal with a different token,
    so one client cannot speak for the whole tree. This keeps `agent.py`
    unchanged — it still calls `dep.pds.decide(request)` — while the right
    token and proof go with each call.

    Without tokens it is a thin pass-through to the single client, which is why
    the two modes share one code path here rather than branching in the agent.
    """

    def __init__(self, default: RemotePolicyDecisionService) -> None:
        self._default = default
        self._by_node: dict[str, RemotePolicyDecisionService] = {}

    def register(self, node_id: str, client: RemotePolicyDecisionService) -> None:
        self._by_node[node_id] = client

    def decide(self, request):
        return self._by_node.get(request.node_id, self._default).decide(request)

    def __getattr__(self, name):
        # Setup calls (tree, reserve, settle, …) belong to the root client.
        return getattr(self._default, name)


class RemoteTree:
    """The task tree, held by the service.

    Every method here is one HTTP call. Note that `reserve` and `settle` are
    separate calls rather than folded into the decision: the PDS stays a pure
    function across the wire, so the component that executes is still the one
    that charges the budget.
    """

    def __init__(self, client: RemotePolicyDecisionService, tree_id: str, root_id: str,
                 pds: Optional[NodeAwarePDS] = None, agent_key=None,
                 root_token: str = "") -> None:
        self._client = client
        self.tree_id = tree_id
        self.root_id = root_id
        self._pds = pds
        self._agent_key = agent_key
        self._root_token = root_token

    def spawn(self, parent_id: str, actor: str, requested: Optional[Grant] = None,
              created_at: int = 0) -> RemoteNode:
        grant = {
            "action_classes": list((requested or Grant()).action_classes),
            "resources": list((requested or Grant()).resources),
            "counterparties": list((requested or Grant()).counterparties),
        }

        if self._root_token:
            # TOKEN MODE. A sub-agent's token comes from exchange and nowhere
            # else, so spawning and getting a token are one act rather than two
            # — which is the point: there is no way to obtain a downstream
            # token that skips the narrowing.
            body = self._client.exchange_token(
                self._root_token, self._agent_key, actor=actor, grant=grant,
            )
            self._pds.register(
                body["node_id"],
                self._client.for_task_token(body["task_token"], self._agent_key),
            )
            return RemoteNode(node_id=body["node_id"])

        body = self._client.spawn(self.tree_id, actor, parent_id, grant)
        return RemoteNode(node_id=body["node_id"])

    def reserve(self, amount_minor: int, at: int = 0) -> str:
        return self._client.reserve(self.tree_id, amount_minor)["handle"]

    def settle(self, handle: str, actual_minor: Optional[int] = None) -> None:
        self._client.settle(self.tree_id, handle, actual_minor)

    def release(self, handle: str) -> None:
        self._client.release(self.tree_id, handle)


class RemoteTreeStore:
    """Only `revoke` is used by the scenarios — the stop button."""

    def __init__(self, client: RemotePolicyDecisionService) -> None:
        self._client = client

    def revoke(self, tree_id: str, at: int, reason: str = "") -> None:
        self._client.revoke_tree(tree_id, reason)


@dataclass
class RemoteMission:
    subject: str


@dataclass
class RemoteDeployment:
    """The same shape `deployment.Deployment` presents to the agent."""

    pds: object
    tree: RemoteTree
    trees: RemoteTreeStore
    binder: IntentBinder
    mission: RemoteMission
    agent_key: SigningKey


def subject_token_for(operator: str) -> str:
    """Get the operator's token from the identity provider, if one is wired.

    In the demo that is `dev_idp.py`, a test double standing in for Okta. The
    PDS does not know the difference and should not: it verifies the token
    against a published JWKS with the issuer and audience checked, exactly as
    it would verify a real one. Unset, the demo falls back to the older
    unverified subject path and the console footer says so.
    """
    idp = os.environ.get("WARRANT_IDP_TOKEN_URL", "")
    if not idp:
        return ""
    request = urllib.request.Request(
        idp,
        data=json.dumps({"subject": operator}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["subject_token"]


def build(base_url: str, tree_id: str = "arcadia-run-1",
          subject: str = deployment.OPERATOR,
          foreign_subject: str = "") -> RemoteDeployment:
    """Set the deployment up through the service's own API.

    This is the integrator's path end to end: register the agent's public key,
    turn the operator's sentence into a signed Mission, open the task tree.
    Three calls, off the hot path, and then every decision is one more.

    The agent's PRIVATE key never leaves this process — only the JWK goes to
    the service. That is not a detail: the whole reason the PDS is a separate
    party is that it does not trust whatever holds that key.
    """
    client = RemotePolicyDecisionService(
        base_url,
        # The SERVICE credential: which component is calling, and with which
        # scopes. Distinct from the subject token below, which says on whose
        # behalf. Without it the PDS answers 401 — it refuses to start
        # unauthenticated unless a deployment says so deliberately.
        credential=os.environ.get("WARRANT_CREDENTIAL", ""),
        # A PROVIDER rather than one fixed token, because the catalogue includes
        # a situation where a different operator's consent is presented. That
        # case has to reach the service as another real user's genuine token —
        # correctly denied on subject binding — rather than as a string this
        # process asserted, which is precisely the control being demonstrated.
        subject_token_provider=subject_token_for if os.environ.get("WARRANT_IDP_TOKEN_URL") else None,
    )
    client.wait_until_ready()
    tree_id = run_scoped(tree_id)

    # The key id is DERIVED FROM THE KEY, not from the agent's role.
    #
    # A kid must name one key forever — the service refuses to rebind one,
    # because every attestation signed under the old material would start
    # reading as a forgery. A fixed id like "arcadia-agent-1" breaks that the
    # first time this process restarts against a still-running service: a new
    # keypair arrives claiming a published name. Naming the key after its own
    # public bytes makes the two agree by construction, and re-registering an
    # identical key becomes a no-op rather than a conflict.
    agent_key = SigningKey.generate("bootstrap")
    thumbprint = digest("warrant.demo.agent-key.v1", agent_key.verify_key().to_jwk()["x"])[:16]
    agent_key = SigningKey.from_raw(f"arcadia-agent-{thumbprint}", agent_key.private_bytes())
    client.register_agent_key(agent_key.verify_key().to_jwk())

    mission_id = f"{tree_id}-mission"  # already run-scoped via tree_id
    client.issue_mission(
        mission_id=mission_id,
        # When an IdP is wired, `issue_mission` swaps this for that user's
        # verified token, so the APPROVING user is whoever the IdP says it is
        # rather than a name this process typed.
        subject=subject,
        instruction=deployment.INSTRUCTION,
        action_classes=[
            "ap.invoice.read", "ap.supplier.lookup",
            "ap.invoice.annotate", "ap.payment.release",
        ],
        resources=list(INVOICES),
        counterparties=list(APPROVED_COUNTERPARTIES),
        budget={
            "amount_minor": deployment.BUDGET_CENTS,
            "currency": CURRENCY,
            "max_calls": 40,
        },
        not_before=deployment.WINDOW_OPENS,
        not_after=deployment.WINDOW_CLOSES,
        max_depth=1,
        issued_at=deployment.WINDOW_OPENS,
    )
    tree = client.open_tree(tree_id, mission_id, deployment.AGENT)

    # A TASK-TREE TOKEN, when the deployment requires one. It says — signed by
    # the control zone — that this key holds this node, so the agent can no
    # longer simply assert a node in its attestation. The same key signs
    # attestations and proves possession of the token, which lets the service
    # check the two came from the same place.
    root_token = ""
    if os.environ.get("WARRANT_TASK_TOKENS", "").lower() in ("required", "1", "true"):
        root_token = client.issue_root_token(tree_id, agent_key)["task_token"]

        # "Another operator's consent is presented" needs restating under task
        # tokens, because the attack it modelled is now structurally impossible:
        # the token binds the subject and the tree TOGETHER, so there is no
        # separate subject field left to swap. What remains is presenting a
        # token from a different operator's task — genuine, correctly signed,
        # and for somebody else's work. The node binding catches it.
        if foreign_subject:
            other_tree = f"{tree_id}-foreign"
            other_mission = f"{other_tree}-mission"
            client.issue_mission(
                mission_id=other_mission, subject=foreign_subject,
                instruction="Settle a different operator's batch.",
                action_classes=["ap.invoice.read", "ap.payment.release"],
                resources=list(INVOICES), counterparties=list(APPROVED_COUNTERPARTIES),
                budget={"amount_minor": deployment.BUDGET_CENTS, "currency": CURRENCY},
                not_before=deployment.WINDOW_OPENS, not_after=deployment.WINDOW_CLOSES,
                issued_at=deployment.WINDOW_OPENS,
            )
            client.open_tree(other_tree, other_mission, deployment.AGENT)
            root_token = client.issue_root_token(other_tree, agent_key)["task_token"]

        client = client.for_task_token(root_token, agent_key)

    pds = NodeAwarePDS(client)

    return RemoteDeployment(
        pds=pds,
        tree=RemoteTree(client, tree_id, tree["root_id"], pds=pds,
                        agent_key=agent_key, root_token=root_token),
        trees=RemoteTreeStore(client),
        binder=IntentBinder(agent_key, tree_id, tree["root_id"]),
        mission=RemoteMission(subject=subject),
        agent_key=agent_key,
    )


def registry_document() -> dict:
    """Arcadia's action classes, in the shape the service loads them from.

    Generated from `world.ACTIONS` rather than written twice, so the file the
    service reads and the registry the embedded demo uses cannot drift — a
    divergence there would make the two modes differ for a reason that has
    nothing to do with the service.
    """
    return {
        "version": ACTIONS.version,
        "actions": [
            {
                "name": name,
                "blast_radius": ACTIONS.get(name).blast_radius,
                "side_effect": ACTIONS.get(name).side_effect,
                "description": ACTIONS.get(name).description,
            }
            for name in ACTIONS.names()
        ],
    }
