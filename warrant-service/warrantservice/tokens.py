"""Task-tree tokens: the only way an agent proves which node it is.

Until now, any registered agent key could sign an attestation naming any node
in any tree. The attestation bound the ARGUMENTS honestly, but node identity
was self-asserted — agent B could attest as a node that belonged to agent A,
and every downstream control would evaluate it against A's grant.

A task-tree token closes that. It says, signed by the control zone: this key
holds this node, in this tree, under this Mission, for this user, with this
actor chain behind it. Four properties, each answering something the spec asks
directly.

**Every token in a task carries the same `tree_id`.** So a token is scoped to
one task rather than to a user or a session, and "which task was this?" is
answerable from the token alone.

**Sub-agents get narrower tokens by exchange only.** `exchange()` is the single
path to a downstream token, and it does not re-implement narrowing — it calls
`TaskTree.spawn`, so the child's grant is the ENGINE's intersection and the
depth cap is the engine's check. A second implementation of narrowing here is
exactly the kind of drift that produces a sub-agent with more authority than
its parent.

**Depth is capped**, by that same call.

**Tokens are proof-of-possession bound**, so a stolen one is useless: the token
carries `cnf.jkt`, the thumbprint of the holder's key, and every presentation
needs a fresh DPoP proof signed by the matching private key (`dpop.py`).

The actor chain is emitted as RFC 8693 `act`, nested innermost-first, because
that is what an OAuth-aware consumer already knows how to read. It is also
checked against the tree's own `actor_chain` at issuance, so the token cannot
describe a delegation path the tree did not actually mint.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import jwt

from warrant import Grant, SigningKey
from warrant.signing import b64u_encode
from warrant.tree import TaskTree, TreeError

ALGORITHM = "EdDSA"
TOKEN_TYPE = "warrant-task+jwt"


class TokenError(Exception):
    """A token did not VERIFY — forged, expired, wrong type, unbound.

    Deliberately narrow. A refusal about the TREE's state (a revoked task, a
    delegation deeper than the Mission allows) is not a statement about the
    token, and conflating them produced a real bug: a depth-cap refusal came
    back as 401, telling an agent its perfectly valid token was invalid. Those
    refusals stay `TreeError` and surface as 409, exactly as they do on the
    node route.
    """


@dataclass(frozen=True)
class TaskToken:
    """A verified token, unpacked into what the service needs from it."""

    raw: str
    tree_id: str
    node_id: str
    mission_id: str
    subject: str
    jkt: str
    depth: int
    actor_chain: tuple[str, ...]
    expires_at: int
    jti: str


def _actor_claim(chain: tuple[str, ...]) -> Optional[dict]:
    """RFC 8693 `act`: nested, with the ORIGINAL actor innermost.

    `("root", "helper")` becomes `{"sub": "helper", "act": {"sub": "root"}}` —
    read outward-in, it is "helper, acting for root".
    """
    if not chain:
        return None
    # Build from the original actor outwards, so each new actor wraps the one
    # it is acting for and the CURRENT actor ends up on top.
    claim: dict[str, Any] = {"sub": chain[0]}
    for actor in chain[1:]:
        claim = {"sub": actor, "act": claim}
    return claim


def actor_chain_of(claim: Optional[dict]) -> tuple[str, ...]:
    """Flatten an `act` claim back to original-first order."""
    chain: list[str] = []
    node = claim
    while isinstance(node, dict) and node.get("sub"):
        chain.append(str(node["sub"]))
        node = node.get("act")
    return tuple(reversed(chain))


class TokenService:
    """Mints and verifies task-tree tokens.

    Holds its OWN signing key, separate from the Mission Authority's. The
    Authority's signature says a human approved a task and is verified by
    resource servers we do not run; this key says an agent holds a node and is
    verified only here. One key for both would mean a compromise of the busy,
    online path also forged consent.
    """

    def __init__(
        self,
        issuer: str,
        signing_key: SigningKey,
        *,
        audience: str = "warrant-pds",
        ttl_seconds: int = 900,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.ttl = ttl_seconds
        self._key = signing_key

    def public_jwk(self) -> dict:
        return self._key.verify_key().to_jwk()

    # -- issuance ---------------------------------------------------------

    def _mint(self, *, tree: TaskTree, node, jkt: str, now: int) -> str:
        if not jkt:
            raise TokenError(
                "a task token must be bound to a key (cnf.jkt). An unbound token "
                "would be a bearer token, and the guarantee here is that a stolen "
                "token is useless"
            )
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": tree.mission.subject,
            "iat": now,
            "exp": now + self.ttl,
            "jti": uuid.uuid4().hex,
            "mission": tree.mission.mission_id,
            "tree_id": tree.tree_id,
            "node_id": node.node_id,
            "depth": node.depth,
            "cnf": {"jkt": jkt},
            "grant": {
                "action_classes": list(node.grant.action_classes),
                "resources": list(node.grant.resources),
                "counterparties": list(node.grant.counterparties),
            },
        }
        act = _actor_claim(node.actor_chain)
        if act:
            claims["act"] = act
        return jwt.encode(
            claims, self._key.private_key, algorithm=ALGORITHM,
            headers={"typ": TOKEN_TYPE, "kid": self._key.key_id},
        )

    def issue_root(self, tree: TaskTree, jkt: str, now: Optional[int] = None) -> str:
        """The task's first token, for the root agent.

        Issued against a tree that already exists, so the Mission has already
        been checked for currency and the tree for the denylist. This endpoint
        creates no authority of its own — it only names, in a verifiable way,
        authority the tree already holds.
        """
        now = int(time.time()) if now is None else now
        if tree.revoked:
            raise TreeError(f"tree {tree.tree_id!r} is revoked; no token will be issued")
        return self._mint(tree=tree, node=tree.node(tree.root_id), jkt=jkt, now=now)

    def exchange(
        self,
        parent: TaskToken,
        tree: TaskTree,
        *,
        actor: str,
        requested: Grant,
        jkt: str,
        now: Optional[int] = None,
    ) -> tuple[str, Any]:
        """A narrower token for a sub-agent. The ONLY path to a downstream token.

        Narrowing and the depth cap come from `tree.spawn`, not from this file.
        That is deliberate: a second implementation of "cannot widen" living
        next to the first is how the two eventually disagree, and the one that
        is wrong is the one an attacker will find.
        """
        now = int(time.time()) if now is None else now
        if not actor:
            raise TokenError("an exchanged token must name the sub-agent it is for")
        # `spawn` refuses a revoked tree and enforces the depth cap, so both
        # arrive here as TreeError and neither is re-wrapped: they are refusals
        # about the task, not about the token that asked.
        child = tree.spawn(parent.node_id, actor, requested, created_at=now)
        return self._mint(tree=tree, node=child, jkt=jkt, now=now), child

    # -- verification -----------------------------------------------------

    def verify(self, token: str, now: Optional[int] = None) -> TaskToken:
        if not token or not isinstance(token, str):
            raise TokenError("a task token is required and must be a string")
        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:
            raise TokenError(f"task token header is unreadable: {exc}") from exc
        if header.get("typ") != TOKEN_TYPE:
            raise TokenError(
                f"task token must carry typ={TOKEN_TYPE!r}, got {header.get('typ')!r}. "
                "The type header is what stops a subject token or a DPoP proof "
                "being presented as a task token"
            )
        try:
            claims = jwt.decode(
                token,
                self._key.verify_key().public_key,
                algorithms=[ALGORITHM],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub", "jti", "tree_id", "node_id"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("task token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError(f"task token did not verify: {exc}") from exc

        jkt = ((claims.get("cnf") or {}).get("jkt")) or ""
        if not jkt:
            raise TokenError(
                "task token carries no cnf.jkt, so it is not bound to a key. "
                "Refusing to treat it as a bearer token"
            )
        return TaskToken(
            raw=token,
            tree_id=str(claims["tree_id"]),
            node_id=str(claims["node_id"]),
            mission_id=str(claims.get("mission") or ""),
            subject=str(claims["sub"]),
            jkt=str(jkt),
            depth=int(claims.get("depth") or 0),
            actor_chain=actor_chain_of(claims.get("act")),
            expires_at=int(claims["exp"]),
            jti=str(claims["jti"]),
        )
