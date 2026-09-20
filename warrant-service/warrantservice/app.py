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

Two things guard the socket itself, and they answer different questions.
`auth.py` decides whether the CALLER may ask — without it, anyone who could
reach the port could mint a Mission naming any subject and collect an allow,
because the Mission Authority is the one component that can widen permissions.
`oidc.py` decides who the END USER is — before it, the subject was a string in
the request body, so the PDS's subject check read like an identity control and
was not one.

State is in-memory and dies with the process. That is a real limit, stated
plainly rather than hidden behind a store interface that implies otherwise:
`warrant/README.md` lists the Task Control Plane's persistence among the
things Phase 1 still needs.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from warrant import (
    Budget,
    CheckResult,
    ContractError,
    Decision,
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
from .auth import Forbidden, Unauthenticated
from .config import ConfigError, Settings, from_env
from .dpop import DpopError, ProofVerifier
from .oidc import SubjectError, SubjectVerifier
from .tokens import TokenError, TokenService


def _now() -> int:
    return int(time.time())


def _problem(status: int, detail: str, kind: str = "invalid_request") -> JSONResponse:
    """One error shape everywhere.

    `kind` is what a client branches on; `detail` is what a human reads. A
    client that has to substring-match prose to handle an error will break the
    first time the prose improves.
    """
    return JSONResponse({"error": kind, "detail": detail}, status_code=status)


def _refuse(attestation, check_id: str, detail: str, mission_id: str = "") -> Decision:
    """A denial produced by the SOCKET rather than by the engine.

    Token validation is a transport concern — the engine has no concept of a
    token — so these refusals cannot come from `PolicyDecisionService`. They are
    still returned as a `Decision` so a gateway has one shape to handle and an
    audit trail has one kind of record; the `token.` prefix on the check id is
    what tells a reader which layer decided.
    """
    check = CheckResult(check_id=check_id, status="violated", detail=detail,
                        on_violation="deny")
    return Decision(
        effect="deny",
        tree_id=attestation.tree_id,
        node_id=attestation.node_id,
        mission_id=mission_id,
        checks=(check,),
        reasons=(check_id,),
        decided_at=int(time.time()),
    )


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

    authenticator = settings.authenticator
    subjects = SubjectVerifier(settings.oidc)
    tokens = TokenService(
        issuer=settings.issuer,
        signing_key=settings.token_key,
        audience=settings.token_audience,
        ttl_seconds=settings.token_ttl_seconds,
    ) if settings.token_key else None
    proofs = ProofVerifier(settings.dpop_window_seconds)

    def scope(name: str):
        """Require a scope on a route.

        Written as a FastAPI dependency so the requirement sits in the route
        DECORATOR, where it is visible next to the path, rather than as a
        line inside a handler that a new route can forget to copy.
        """

        async def guard(request: Request):
            client = authenticator.authenticate(request.headers.get("authorization"))
            authenticator.require(client, name)
            # Stashed for handlers that want to attribute an action to a caller.
            request.state.client = client
            return client

        return Depends(guard)

    def resolve_subject(body: dict, *, where: str) -> str:
        """The end user, from the IdP when one is configured.

        The two paths are mutually exclusive on purpose. With an issuer
        configured, a body carrying `token_subject` is REFUSED rather than
        ignored: leaving the unverified path open beside the verified one means
        the weakest link is still there for whoever finds it first.
        """
        if subjects.enabled:
            if "token_subject" in body:
                raise codec.WireError(
                    f"{where}.token_subject is not accepted: this deployment "
                    "verifies the end user against an identity provider. Send "
                    "the IdP's token as `subject_token` instead"
                )
            return subjects.subject_of(body.get("subject_token") or "")
        if "subject_token" in body:
            raise codec.WireError(
                f"{where}.subject_token was sent but no identity provider is "
                "configured (WARRANT_OIDC_ISSUER is unset), so it cannot be "
                "verified. Refusing rather than accepting a token nobody checked"
            )
        return codec._str(body, "token_subject", where=where)

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
    app.state.authenticator = authenticator
    app.state.subjects = subjects

    @app.exception_handler(Unauthenticated)
    async def _unauthenticated(_: Request, exc: Unauthenticated):
        # 401 with a WWW-Authenticate header, so a client knows what to present
        # rather than guessing from a bare status.
        return JSONResponse(
            {"error": "unauthenticated", "detail": str(exc)},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(Forbidden)
    async def _forbidden(_: Request, exc: Forbidden):
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

    @app.exception_handler(TokenError)
    async def _token(_: Request, exc: TokenError):
        # 401, like a bad subject token: the request was well formed, the
        # holder could not be established.
        return JSONResponse({"error": "token_not_valid", "detail": str(exc)}, status_code=401)

    @app.exception_handler(DpopError)
    async def _dpop(_: Request, exc: DpopError):
        return JSONResponse(
            {"error": "proof_not_valid", "detail": str(exc)},
            status_code=401,
            headers={"WWW-Authenticate": "DPoP"},
        )

    @app.exception_handler(SubjectError)
    async def _subject(_: Request, exc: SubjectError):
        # 401, not 400: the request was well formed, the identity was not
        # established. A gateway retrying a 400 would retry forever.
        return JSONResponse({"error": "subject_not_verified", "detail": str(exc)},
                            status_code=401)
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
            # Was mislabelled "trees"; it has always been the denylist size.
            "revoked_trees": len(trees.denylist()),
            "agent_keys": len(agent_keys),
            # Posture, reported for as long as it lasts. An operator should be
            # able to see an open door in one request rather than infer it.
            "caller_auth": "required" if authenticator.enabled else "DISABLED",
            "task_tokens": (
                "required" if settings.task_tokens_required
                else "disabled (an agent key may assert any node)"
            ),
            "subject_identity": (
                f"verified against {settings.oidc.issuer}" if subjects.enabled
                else "UNVERIFIED (no identity provider configured)"
            ),
        }

    @app.get("/v1/registry", dependencies=[scope("read")])
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

    @app.post("/v1/agent-keys", dependencies=[scope("agent-key:register")])
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

    @app.get("/v1/agent-keys", dependencies=[scope("read")])
    def list_agent_keys() -> dict:
        return agent_keys.to_jwks()

    # -- missions ----------------------------------------------------------

    @app.post("/v1/missions", dependencies=[scope("mission:issue")])
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
                {"mission_id", "subject", "subject_token", "instruction", "action_classes",
                 "resources", "counterparties", "budget", "not_before", "not_after",
                 "max_depth", "issued_at", "supersedes"},
                where="body",
            )
            # The Mission's subject is WHO APPROVED. With an IdP configured it
            # comes from their verified token; minting consent on behalf of a
            # name someone typed was the hole this closes.
            if subjects.enabled:
                if "subject" in body:
                    raise codec.WireError(
                        "body.subject is not accepted: this deployment verifies "
                        "the approving user against an identity provider. Send "
                        "their IdP token as `subject_token`"
                    )
                subject = subjects.subject_of(body.get("subject_token") or "")
            else:
                if "subject_token" in body:
                    raise codec.WireError(
                        "body.subject_token was sent but no identity provider is "
                        "configured, so it cannot be verified"
                    )
                subject = codec._str(body, "subject", where="body")
            signed = authority.issue(
                mission_id=codec._str(body, "mission_id", where="body"),
                subject=subject,
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

    @app.get("/v1/missions/{mission_id}", dependencies=[scope("read")])
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

    @app.post("/v1/missions/{mission_id}/revoke", dependencies=[scope("mission:issue")])
    def revoke_mission(mission_id: str):
        authority.revoke(mission_id, _now())
        return {"mission_id": mission_id, "current": authority.is_current(mission_id)}

    @app.post("/v1/missions/verify", dependencies=[scope("read")])
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

    @app.post("/v1/trees", dependencies=[scope("tree:manage")])
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

    @app.get("/v1/trees/{tree_id}", dependencies=[scope("read")])
    def get_tree(tree_id: str):
        try:
            return _tree_state(trees.get(tree_id))
        except TreeError as exc:
            return _problem(404, str(exc), kind="not_found")

    @app.post("/v1/trees/{tree_id}/nodes", dependencies=[scope("tree:manage")])
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

    @app.post("/v1/trees/{tree_id}/revoke", dependencies=[scope("tree:revoke")])
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

    @app.post("/v1/trees/{tree_id}/reservations", dependencies=[scope("tree:manage")])
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

    @app.post("/v1/trees/{tree_id}/reservations/{handle}/settle", dependencies=[scope("tree:manage")])
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

    @app.post("/v1/trees/{tree_id}/reservations/{handle}/release", dependencies=[scope("tree:manage")])
    def release(tree_id: str, handle: str):
        try:
            tree = trees.get(tree_id)
            tree.release(handle)
        except TreeError as exc:
            return _problem(409, str(exc), kind="release_refused")
        return {"handle": handle, "remaining": tree.remaining_minor()}

    # -- revocation replication -------------------------------------------

    @app.get("/v1/denylist", dependencies=[scope("read")])
    def get_denylist() -> dict:
        """The replicable set: tree id to revocation time.

        Small and append-only by design, because the spec requires revocation
        to reach every PDS within five seconds "even when an issuer does not
        cooperate" — which means peers exchange this, not whole trees.
        """
        return {"entries": trees.denylist()}

    @app.post("/v1/denylist", dependencies=[scope("denylist:merge")])
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

    # -- task-tree tokens --------------------------------------------------

    def _token_response(raw: str, node=None) -> dict:
        body = {"task_token": raw, "token_type": "DPoP", "expires_in": settings.token_ttl_seconds}
        if node is not None:
            body["node_id"] = node.node_id
            body["depth"] = node.depth
            body["actor_chain"] = list(node.actor_chain)
            body["grant"] = {
                "action_classes": list(node.grant.action_classes),
                "resources": list(node.grant.resources),
                "counterparties": list(node.grant.counterparties),
            }
        return body

    @app.post("/v1/token", dependencies=[scope("token:issue")])
    async def issue_root_token(request: Request):
        """The task's first token, for the root agent.

        Creates no authority: the tree already exists, so the Mission has been
        checked for currency and the tree for the denylist. This only names, in
        a verifiable and key-bound way, authority the tree already holds.
        """
        if tokens is None:
            return _problem(501, "no token signing key is configured", kind="not_configured")
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(body, {"tree_id", "jkt"}, where="body")
            tree = trees.get(codec._str(body, "tree_id", where="body"))
        except codec.WireError as exc:
            return _problem(400, str(exc))
        except TreeError as exc:
            return _problem(404, str(exc), kind="not_found")
        try:
            raw = tokens.issue_root(tree, codec._str(body, "jkt", where="body"))
        except codec.WireError as exc:
            return _problem(400, str(exc))
        except TreeError as exc:
            # A refusal about the TASK's state (revoked), not about a token.
            return _problem(409, str(exc), kind="token_refused")
        return _token_response(raw, tree.node(tree.root_id))

    @app.post("/v1/token/exchange")
    async def exchange_token(request: Request):
        """A narrower token for a sub-agent — the only path to a downstream one.

        Deliberately NOT behind a service-credential scope. The parent token
        plus a proof of possessing its key IS the authorization, which is the
        OAuth token-exchange model (RFC 8693): an agent process holds its task
        token, not a deployment credential. Presenting someone else's token
        fails at the proof.

        Narrowing and the depth cap are `TaskTree.spawn`'s, never re-implemented
        here.
        """
        if tokens is None:
            return _problem(501, "no token signing key is configured", kind="not_configured")
        try:
            body = codec._require(await body_of(request), where="body")
            codec._only(body, {"task_token", "dpop_proof", "actor", "grant", "jkt"},
                        where="body")
            parent_raw = codec._str(body, "task_token", where="body")
            parent = tokens.verify(parent_raw)

            proof = proofs.verify(
                codec._str(body, "dpop_proof", where="body"),
                method="POST",
                url=str(request.url),
                access_token=parent_raw,
            )
            # The proof must be made with the key the PARENT token is bound to.
            # Without this check the exchange would accept any well-formed proof
            # alongside a stolen token — which is precisely what binding exists
            # to prevent, so it is the one line that makes the rest worth having.
            if proof.jkt != parent.jkt:
                raise DpopError(
                    "the DPoP proof was made with a different key than the task "
                    "token is bound to, so whoever presented this token does not "
                    "hold its key"
                )

            try:
                tree = trees.get(parent.tree_id)
            except TreeError as exc:
                return _problem(404, str(exc), kind="not_found")
            grant_body = codec._require(body.get("grant") or {}, where="grant")
            codec._only(grant_body, {"action_classes", "resources", "counterparties"},
                        where="grant")
            raw, child = tokens.exchange(
                parent, tree,
                actor=codec._str(body, "actor", where="body"),
                requested=Grant(
                    action_classes=codec._strs(grant_body, "action_classes", where="grant"),
                    resources=codec._strs(grant_body, "resources", where="grant"),
                    counterparties=codec._strs(grant_body, "counterparties", where="grant"),
                ),
                jkt=codec._str(body, "jkt", where="body") or parent.jkt,
            )
        except codec.WireError as exc:
            return _problem(400, str(exc))
        except TreeError as exc:
            # Depth cap, or a revoked task. The token was fine; the request was
            # not, and 401 would tell an agent its valid token was invalid.
            return _problem(409, str(exc), kind="exchange_refused")
        return _token_response(raw, child)

    # -- the hot path ------------------------------------------------------

    @app.post("/v1/decisions", dependencies=[scope("decide")])
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
                {"signed_attestation", "observed_args", "node_id", "now",
                 "token_subject", "subject_token",
                 "task_token", "dpop_proof", "htm", "htu"},
                where="body",
            )
            signed_att = codec.signed_attestation_from_wire(body.get("signed_attestation"))

            if settings.task_tokens_required and tokens is not None:
                # With tokens required, node identity and the end user both come
                # from the token. The caller may no longer assert either — the
                # self-asserted path is closed, the same way configuring an IdP
                # closes the unverified subject path.
                for asserted in ("node_id", "token_subject", "subject_token"):
                    if asserted in body:
                        raise codec.WireError(
                            f"body.{asserted} is not accepted: this deployment "
                            "requires a task-tree token, which already says which "
                            "node is calling and for whom"
                        )
                raw_token = codec._str(body, "task_token", where="body")
                task = tokens.verify(raw_token)

                # The proof is made by the AGENT for the call it is making, and
                # the gateway reports the method and URL it actually saw. That
                # is what stops a proof captured at a harmless endpoint from
                # being presented at a dangerous one.
                proof = proofs.verify(
                    codec._str(body, "dpop_proof", where="body"),
                    method=codec._str(body, "htm", where="body") or "POST",
                    url=codec._str(body, "htu", where="body"),
                    access_token=raw_token,
                )
                if proof.jkt != task.jkt:
                    raise DpopError(
                        "the DPoP proof was made with a different key than the "
                        "task token is bound to"
                    )

                att = signed_att.attestation
                if (att.tree_id, att.node_id) != (task.tree_id, task.node_id):
                    # A refusal the ENGINE cannot make, because it has no
                    # concept of tokens. Returned as a Decision rather than an
                    # error so the record looks like every other refusal, with
                    # a `token.` check id saying which layer produced it.
                    return codec.decision_to_wire(_refuse(
                        att, "token.node_binding",
                        f"attestation claims {att.tree_id}/{att.node_id} but the "
                        f"task token holds {task.tree_id}/{task.node_id}: an agent "
                        "cannot act as a node it was not issued",
                        task.mission_id,
                    ))
                subject = task.subject
                node_id = task.node_id
            else:
                if "task_token" in body:
                    raise codec.WireError(
                        "body.task_token was sent but this deployment does not "
                        "require task-tree tokens (WARRANT_TASK_TOKENS is unset), "
                        "so it would not be enforced. Refusing rather than "
                        "accepting a token nobody checked"
                    )
                subject = resolve_subject(body, where="body")
                node_id = codec._str(body, "node_id", where="body")
            observed = body.get("observed_args")
            if not isinstance(observed, dict):
                raise codec.WireError("observed_args must be an object of the arguments the tool received")
            decision = pds.decide(
                DecisionRequest(
                    signed=signed_att,
                    observed_args=observed,
                    node_id=node_id,
                    now=codec._int(body, "now", where="body", default=_now()),
                    token_subject=subject,
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
