"""The Policy Decision Service, over HTTP.

The engine decides; this file is the socket it decides through. Two things
shape every route below.

**The hot path is `POST /v1/decisions` and nothing else.** The spec budgets
2 ms for a decision inside a 3 ms per-call allowance, so that route does no
I/O, allocates no store, and reads the same in-memory state the engine already
holds. Everything else here — issuing Missions, spawning nodes, settling
budget — happens off the call path and is allowed to be ordinary.

**A refusal is a 200 with a decision, not an error status.** A denied call is
the service working correctly, and a gateway that has to distinguish "denied"
from "service broken" by reading an HTTP status will eventually get it wrong in
the permissive direction. 4xx is reserved for a malformed request — a body the
service could not interpret at all — and even then the body names the field.

State is in-memory and dies with the process. That is a real limit, stated
plainly rather than hidden behind a store interface that implies otherwise:
`warrant/README.md` lists the Task Control Plane's persistence among the
things Phase 1 still needs.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from warrant import (
    Budget,
    ContractError,
    DecisionRequest,
    Grant,
    KeySet,
    MissionAuthority,
    PolicyDecisionService,
    TreeStore,
    VerificationError,
    VerifyKey,
    instruction_hash,
)
from warrant.tree import BudgetExceeded, TreeError

from . import codec
from .config import ConfigError, Settings, from_env


def _now() -> int:
    return int(time.time())


def _problem(status: int, detail: str, kind: str = "invalid_request") -> JSONResponse:
    """One error shape everywhere.

    `kind` is what a client branches on; `detail` is what a human reads. A
    client that has to substring-match prose to handle an error will break the
    first time the prose improves.
    """
    return JSONResponse({"error": kind, "detail": detail}, status_code=status)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or from_env()

    app = FastAPI(
        title="Warrant — Policy Decision Service",
        version="1",
        description=(
            "Prefront's enforcement plane. Issue a Mission from a human's approval, "
            "open a task tree, and put every side-effect call to POST /v1/decisions "
            "before it happens."
        ),
    )

    authority = MissionAuthority(settings.issuer, settings.authority_key)
    trees = TreeStore()
    agent_keys = KeySet()
    pds = PolicyDecisionService(
        trees=trees,
        agent_keys=agent_keys,
        registry=settings.registry,
        policy_version=settings.policy_version,
    )
    app.state.settings = settings
    app.state.authority = authority
    app.state.trees = trees
    app.state.agent_keys = agent_keys
    app.state.pds = pds

    async def body_of(request: Request) -> Any:
        try:
            return await request.json()
        except Exception as exc:
            raise codec.WireError(f"request body is not valid JSON: {exc}") from exc

    # -- health and introspection -----------------------------------------

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "ok": True,
            "issuer": settings.issuer,
            "policy_version": settings.policy_version,
            "actions": len(settings.registry),
            "trees": len(trees.denylist()) + 0,
            "agent_keys": len(agent_keys),
        }

    @app.get("/v1/registry")
    def registry() -> dict:
        """What this deployment's vocabulary actually is.

        Worth exposing because an empty registry is a legitimate but
        consequential state: every side-effect call will fail closed, and an
        operator staring at blanket denials needs to be able to see that in one
        request rather than infer it.
        """
        return {
            "version": settings.registry.version,
            "configured": bool(settings.registry_path),
            "source": settings.registry_path or None,
            "actions": [
                {
                    "name": name,
                    "blast_radius": settings.registry.get(name).blast_radius,
                    "side_effect": settings.registry.get(name).side_effect,
                    "description": settings.registry.get(name).description,
                }
                for name in settings.registry.names()
            ],
        }

    @app.get("/.well-known/jwks.json")
    def jwks() -> dict:
        """The Authority's public keys, so anyone can verify a Mission offline.

        Served unauthenticated by design: these are public keys, and the spec's
        whole point is that a resource server we do not run — possibly in
        another organisation, possibly air-gapped with a copy of this document —
        can check a Mission without asking us anything.
        """
        return authority.jwks()

    # -- agent keys --------------------------------------------------------

    @app.post("/v1/agent-keys")
    async def add_agent_key(request: Request):
        """Register an agent process's public key.

        The PDS verifies attestations against exactly these. Note what this
        does NOT do: it never accepts a key id that is already published under
        different material, because a kid must name one key forever or every
        attestation signed under the old one starts reading as a forgery.
        """
        try:
            body = await body_of(request)
            key = VerifyKey.from_jwk(body)
            agent_keys.add(key)
        except (codec.WireError, VerificationError) as exc:
            return _problem(400, str(exc))
        except Exception as exc:
            return _problem(409, str(exc), kind="key_conflict")
        return {"key_id": key.key_id, "registered": True}

    @app.get("/v1/agent-keys")
    def list_agent_keys() -> dict:
        return agent_keys.to_jwks()

    # -- missions ----------------------------------------------------------

    @app.post("/v1/missions")
    async def issue_mission(request: Request):
        """Turn one human approval into a signed Mission.

        Takes the operator's INSTRUCTION as text, never a hash. The consent
        screen showed them those words, and accepting a hash would let the
        thing signed drift from the thing displayed — the one lie a Mission
        exists to prevent.
        """
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(
                body,
                {"mission_id", "subject", "instruction", "action_classes", "resources",
                 "counterparties", "budget", "not_before", "not_after", "max_depth",
                 "issued_at", "supersedes"},
                where="body",
            )
            signed = authority.issue(
                mission_id=codec._str(body, "mission_id", where="body"),
                subject=codec._str(body, "subject", where="body"),
                instruction=codec._str(body, "instruction", where="body"),
                action_classes=codec._strs(body, "action_classes", where="body"),
                resources=codec._strs(body, "resources", where="body"),
                counterparties=codec._strs(body, "counterparties", where="body"),
                budget=codec.budget_from_wire(body.get("budget")),
                not_before=codec._int(body, "not_before", where="body"),
                not_after=codec._int(body, "not_after", where="body"),
                max_depth=codec._int(body, "max_depth", where="body"),
                issued_at=codec._int(body, "issued_at", where="body", default=_now()),
                supersedes=codec._str(body, "supersedes", where="body"),
            )
        except (codec.WireError, ContractError) as exc:
            return _problem(400, str(exc))
        return codec.signed_mission_to_wire(signed)

    @app.get("/v1/missions/{mission_id}")
    def get_mission(mission_id: str):
        try:
            signed = authority.get(mission_id)
        except VerificationError as exc:
            return _problem(404, str(exc), kind="not_found")
        return {
            **codec.signed_mission_to_wire(signed),
            "current": authority.is_current(mission_id),
            "superseded_by": authority.superseded_by(mission_id) or None,
        }

    @app.post("/v1/missions/{mission_id}/revoke")
    def revoke_mission(mission_id: str):
        authority.revoke(mission_id, _now())
        return {"mission_id": mission_id, "current": authority.is_current(mission_id)}

    @app.post("/v1/missions/verify")
    async def verify_mission(request: Request):
        """What a resource server calls if it would rather not verify locally.

        Offered for convenience, never as the only way: the JWKS above exists so
        this call is optional, and a deployment that can verify offline should.
        Supersession is the reason this is not simply a signature check — a
        replaced Mission stays cryptographically valid forever, so something has
        to remember the user changed their mind.
        """
        try:
            signed = codec.signed_mission_from_wire(await body_of(request))
        except codec.WireError as exc:
            return _problem(400, str(exc))
        try:
            authority.verify(signed)
        except VerificationError as exc:
            return {"valid": False, "reason": str(exc)}
        return {"valid": True, "reason": ""}

    # -- task trees --------------------------------------------------------

    def _tree_state(tree) -> dict:
        return {
            "tree_id": tree.tree_id,
            "mission_id": tree.mission.mission_id,
            "root_id": tree.root_id,
            "revoked": tree.revoked,
            "revoked_reason": tree.revoked_reason,
            "nodes": [
                {
                    "node_id": n.node_id, "actor": n.actor, "depth": n.depth,
                    "parent_id": n.parent_id, "actor_chain": list(n.actor_chain),
                    "grant": {
                        "action_classes": list(n.grant.action_classes),
                        "resources": list(n.grant.resources),
                        "counterparties": list(n.grant.counterparties),
                    },
                }
                for n in tree.nodes()
            ],
            "budget": {
                "amount_minor": tree.budget.amount_minor,
                "currency": tree.budget.currency,
                "max_calls": tree.budget.max_calls,
                "committed": tree.spend_committed,
                "outstanding": tree.spend_outstanding,
                "remaining": tree.remaining_minor(),
                "calls": tree.call_count,
            },
        }

    @app.post("/v1/trees")
    async def open_tree(request: Request):
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(body, {"tree_id", "mission_id", "root_actor"}, where="body")
            mission_id = codec._str(body, "mission_id", where="body")
            signed = authority.get(mission_id)
            if not authority.is_current(mission_id):
                return _problem(
                    409,
                    f"Mission {mission_id!r} is no longer current "
                    f"(superseded by {authority.superseded_by(mission_id)!r} or revoked)",
                    kind="mission_not_current",
                )
            tree = trees.create(
                tree_id=codec._str(body, "tree_id", where="body"),
                mission=signed.mission,
                root_actor=codec._str(body, "root_actor", where="body"),
                created_at=_now(),
            )
        except VerificationError as exc:
            return _problem(404, str(exc), kind="not_found")
        except (codec.WireError, ContractError) as exc:
            return _problem(400, str(exc))
        except TreeError as exc:
            return _problem(409, str(exc), kind="tree_conflict")
        return _tree_state(tree)

    @app.get("/v1/trees/{tree_id}")
    def get_tree(tree_id: str):
        try:
            return _tree_state(trees.get(tree_id))
        except TreeError as exc:
            return _problem(404, str(exc), kind="not_found")

    @app.post("/v1/trees/{tree_id}/nodes")
    async def spawn_node(tree_id: str, request: Request):
        """Mint a sub-agent's node — the only way one gets a token.

        The requested grant cannot widen: the engine intersects it with the
        parent's, and a request for an unconstrained grant inherits the
        parent's list rather than the world's.
        """
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(body, {"parent_id", "actor", "grant"}, where="body")
            grant_body = body.get("grant") or {}
            codec._only(
                codec._require(grant_body, where="grant"),
                {"action_classes", "resources", "counterparties"},
                where="grant",
            )
            tree = trees.get(tree_id)
            node = tree.spawn(
                parent_id=codec._str(body, "parent_id", where="body") or tree.root_id,
                actor=codec._str(body, "actor", where="body"),
                requested=Grant(
                    action_classes=codec._strs(grant_body, "action_classes", where="grant"),
                    resources=codec._strs(grant_body, "resources", where="grant"),
                    counterparties=codec._strs(grant_body, "counterparties", where="grant"),
                ),
                created_at=_now(),
            )
        except codec.WireError as exc:
            return _problem(400, str(exc))
        except TreeError as exc:
            # Depth-cap and revoked-tree refusals land here. 409 rather than
            # 403: this is a statement about the tree's state, not a decision
            # about a call — decisions only ever come from /v1/decisions.
            return _problem(409, str(exc), kind="spawn_refused")
        return {
            "node_id": node.node_id, "depth": node.depth, "actor": node.actor,
            "actor_chain": list(node.actor_chain),
            "grant": {
                "action_classes": list(node.grant.action_classes),
                "resources": list(node.grant.resources),
                "counterparties": list(node.grant.counterparties),
            },
        }

    @app.post("/v1/trees/{tree_id}/revoke")
    async def revoke_tree(tree_id: str, request: Request):
        """The stop button. Works for a tree this replica has never seen."""
        try:
            body = await body_of(request) if await request.body() else {}
            reason = codec._str(codec._require(body or {}, where="body"), "reason", where="body")
        except codec.WireError as exc:
            return _problem(400, str(exc))
        trees.revoke(tree_id, _now(), reason)
        return {"tree_id": tree_id, "revoked": True, "reason": reason}

    # -- the budget ledger -------------------------------------------------

    @app.post("/v1/trees/{tree_id}/reservations")
    async def reserve(tree_id: str, request: Request):
        """Hold budget for a call that is about to execute.

        Separate from deciding on purpose. The PDS is a pure function so a
        proposed policy can be replayed against recorded attestations; if it
        reserved, it would charge the task for calls a later control denied.
        """
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(body, {"amount_minor"}, where="body")
            tree = trees.get(tree_id)
            handle = tree.reserve(codec._int(body, "amount_minor", where="body"), at=_now())
        except codec.WireError as exc:
            return _problem(400, str(exc))
        except BudgetExceeded as exc:
            return _problem(409, str(exc), kind="budget_exceeded")
        except TreeError as exc:
            return _problem(409, str(exc), kind="tree_conflict")
        return {"handle": handle, "remaining": tree.remaining_minor()}

    @app.post("/v1/trees/{tree_id}/reservations/{handle}/settle")
    async def settle(tree_id: str, handle: str, request: Request):
        try:
            raw = await request.body()
            body = codec._require(await body_of(request), where="body") if raw else {}
            codec._only(body, {"actual_minor"}, where="body")
            tree = trees.get(tree_id)
            tree.settle(handle, body.get("actual_minor"))
        except codec.WireError as exc:
            return _problem(400, str(exc))
        except TreeError as exc:
            return _problem(409, str(exc), kind="settle_refused")
        return {"handle": handle, "committed": tree.spend_committed,
                "remaining": tree.remaining_minor()}

    @app.post("/v1/trees/{tree_id}/reservations/{handle}/release")
    def release(tree_id: str, handle: str):
        try:
            tree = trees.get(tree_id)
            tree.release(handle)
        except TreeError as exc:
            return _problem(409, str(exc), kind="release_refused")
        return {"handle": handle, "remaining": tree.remaining_minor()}

    # -- revocation replication -------------------------------------------

    @app.get("/v1/denylist")
    def get_denylist() -> dict:
        """The replicable set: tree id to revocation time.

        Small and append-only by design, because the spec requires revocation
        to reach every PDS within five seconds "even when an issuer does not
        cooperate" — which means peers exchange this, not whole trees.
        """
        return {"entries": trees.denylist()}

    @app.post("/v1/denylist")
    async def merge_denylist(request: Request):
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(body, {"entries"}, where="body")
            entries = body.get("entries") or {}
            if not isinstance(entries, dict) or any(
                not isinstance(v, int) or isinstance(v, bool) for v in entries.values()
            ):
                raise codec.WireError("entries must map tree_id -> integer epoch seconds")
            added = trees.merge_denylist(entries)
        except codec.WireError as exc:
            return _problem(400, str(exc))
        return {"added": added, "total": len(trees.denylist())}

    # -- the hot path ------------------------------------------------------

    @app.post("/v1/decisions")
    async def decide(request: Request):
        """Allow, deny or step up. The only route on a tool call's path.

        A refusal returns 200 with `effect: "deny"`. That is not sloppiness:
        a denial is this service succeeding, and a caller who distinguishes
        "denied" from "service unreachable" by HTTP status will one day treat
        a 503 as a deny — or, far worse, a deny as a transport blip to retry.
        """
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(
                body,
                {"signed_attestation", "observed_args", "node_id", "now", "token_subject"},
                where="body",
            )
            observed = body.get("observed_args")
            if not isinstance(observed, dict):
                raise codec.WireError("observed_args must be an object of the arguments the tool received")
            decision = pds.decide(
                DecisionRequest(
                    signed=codec.signed_attestation_from_wire(body.get("signed_attestation")),
                    observed_args=observed,
                    node_id=codec._str(body, "node_id", where="body"),
                    now=codec._int(body, "now", where="body", default=_now()),
                    token_subject=codec._str(body, "token_subject", where="body"),
                )
            )
        except codec.WireError as exc:
            return _problem(400, str(exc))
        return codec.decision_to_wire(decision)

    return app


app = None  # built by `main()` / uvicorn factory so import never reads the env


def build() -> FastAPI:
    """Factory for `uvicorn warrantservice.app:build --factory`."""
    return create_app()
