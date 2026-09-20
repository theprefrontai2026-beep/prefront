"""A client for the PDS, using the standard library and nothing else.

This is what goes into a customer's agent process, so its dependency list is
the point. An SDK that dragged in `requests`, `httpx` or a generated stub would
be a dependency conflict waiting to happen in exactly the process least able to
absorb one — the spec's promise is "under 50 lines to integrate", not "under 50
lines plus a resolution argument with your existing HTTP library".

`RemotePolicyDecisionService.decide()` takes the same `DecisionRequest` and
returns the same `Decision` as the in-process engine. That is deliberate: a
caller should be able to move a PDS in or out of its own process by changing
one construction line, and nothing else. `warrant-demo` does exactly that, which
is how this client stays honest — if the remote path drifted from the embedded
one, twelve scenarios would stop matching their documented outcomes.

**Failure is not permission.** Every transport error raises. A client that
returned "allow" on a timeout — or let a caller mistake `None` for a decision —
would turn an outage into a blanket authorization, which is the single worst
thing this file could do. The spec settles the open question the same way:
fail closed. A 401 or 403 raises through the same path, so a misconfigured
credential fails closed rather than quietly degrading.

Two credentials travel here and they answer different questions. The SERVICE
credential (`Authorization: Bearer <client_id>.<secret>`) says which component
is calling and which scopes it holds. The SUBJECT token says on whose behalf —
and comes from the customer's IdP, never from this library.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from warrant import Decision, DecisionRequest

from . import codec


class ServiceError(RuntimeError):
    """The service could not be reached, or refused the request itself.

    Distinct from a denial. A denial is a `Decision` with `effect="deny"` and
    arrives as a normal result; this is the service failing to answer at all,
    and a caller must treat it as fail-closed rather than as a verdict.
    """


class RemotePolicyDecisionService:
    """The PDS, over HTTP, with the engine's own interface."""

    def __init__(
        self,
        base_url: str,
        *,
        credential: str = "",
        subject_token: str = "",
        subject_token_provider: Optional[Callable[[str], str]] = None,
        task_token: str = "",
        dpop_key: Any = None,
        tool_url: str = "",
        timeout: float = 5.0,
    ) -> None:
        """`credential` is `<client_id>.<secret>`, as minted by
        `python -m warrantservice.credentials new`.

        `subject_token` is the END USER's token from the customer's IdP, and is
        a different thing entirely: the credential says which COMPONENT is
        calling, the subject token says on whose behalf. A deployment with an
        identity provider configured must send the second, and the service
        refuses the old unverified `token_subject` field outright.

        Held on the client rather than passed per call because an agent process
        holds one of each for the life of a task, and threading them through
        every call site is how one gets forgotten.

        A GATEWAY serves many users, so it cannot hold one fixed subject token.
        For that, pass `subject_token_provider`: a callable taking the subject
        named on a `DecisionRequest` and returning that user's token. It takes
        precedence over `subject_token`, and its results are cached per subject
        so a busy gateway does not re-ask the IdP on every call.

        `task_token` and `dpop_key` are the third thing: proof of WHICH NODE is
        calling. The token says so, and the key proves the token is ours — the
        agent's `SigningKey` serves as both the attestation signer and the DPoP
        key, so the service can check that whoever proved possession is whoever
        signed the claim. `tool_url` is the endpoint the proof is made for; it
        defaults to this service's decision route, which is right when the PDS
        is the enforcement point, and a gateway in front of tools should set it
        to the tool URL it actually received.
        """
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.subject_token = subject_token
        self.subject_token_provider = subject_token_provider
        self._subject_tokens: dict[str, str] = {}
        self.task_token = task_token
        self.dpop_key = dpop_key
        self.tool_url = tool_url or f"{self.base_url}/v1/decisions"
        self.timeout = timeout

    # -- transport --------------------------------------------------------

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.credential:
            headers["Authorization"] = f"Bearer {self.credential}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise ServiceError(f"{method} {path} -> {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(
                f"{method} {path} could not reach {self.base_url}: {exc.reason}. "
                "Treat this as a denial, never as an allow"
            ) from exc

    # -- the hot path -----------------------------------------------------

    def _subject_token_for(self, subject: str) -> str:
        """The token to present for `subject`, or "" to use the legacy field.

        Never invents one. If no provider and no fixed token are configured,
        the caller is talking to a deployment with no identity provider, and
        the unverified `token_subject` field is the honest thing to send — the
        service refuses it outright when an issuer IS configured, so the two
        cannot be confused.
        """
        if self.subject_token_provider is not None:
            if subject not in self._subject_tokens:
                self._subject_tokens[subject] = self.subject_token_provider(subject)
            return self._subject_tokens[subject]
        return self.subject_token

    def decide(self, request: DecisionRequest) -> Decision:
        """Identical in signature and return type to the embedded engine.

        The signature has to stay identical — it is what lets a caller move the
        PDS in or out of its own process by changing one construction line, and
        what makes the parity suite a real comparison rather than two different
        APIs producing similar numbers. So everything token-related is read
        from the client, never added as a parameter here.
        """
        signed = request.signed

        # With a task token, node identity and the subject come from the token
        # and must NOT also be asserted in the body; the service refuses both.
        if self.task_token:
            from .dpop import make_proof

            if self.dpop_key is None:
                raise ServiceError(
                    "a task token was supplied without a dpop_key. The token is "
                    "bound to a key, and presenting it without proving possession "
                    "would be presenting a bearer token"
                )
            identity = {
                "task_token": self.task_token,
                "dpop_proof": make_proof(
                    self.dpop_key, method="POST", url=self.tool_url,
                    access_token=self.task_token,
                ),
                "htm": "POST",
                "htu": self.tool_url,
            }
        else:
            identity = {
                "node_id": request.node_id,
                **({"subject_token": token}
                   if (token := self._subject_token_for(request.token_subject))
                   else {"token_subject": request.token_subject}),
            }

        body = self._call(
            "POST", "/v1/decisions",
            {
                "signed_attestation": {
                    "attestation": signed.attestation.to_payload(),
                    "key_id": signed.key_id,
                    "signature": signed.signature,
                    "algorithm": signed.algorithm,
                },
                "observed_args": request.observed_args,
                "now": request.now,
                **identity,
            },
        )
        return codec.decision_from_wire(body)

    # -- setup, off the call path -----------------------------------------

    def health(self) -> dict:
        return self._call("GET", "/healthz")

    def registry(self) -> dict:
        return self._call("GET", "/v1/registry")

    def jwks(self) -> dict:
        return self._call("GET", "/.well-known/jwks.json")

    def register_agent_key(self, jwk: dict) -> dict:
        return self._call("POST", "/v1/agent-keys", jwk)

    def issue_mission(self, **fields: Any) -> dict:
        """Mint a Mission. When this client holds a subject token, the
        APPROVING user is taken from it rather than from a name in `fields` —
        the caller cannot mint consent on behalf of someone they have not
        authenticated."""
        token = self.subject_token or (
            self._subject_token_for(fields["subject"]) if "subject" in fields else ""
        )
        if token and "subject" in fields:
            fields = {k: v for k, v in fields.items() if k != "subject"}
            fields["subject_token"] = token
        elif token:
            fields = {**fields, "subject_token": token}
        return self._call("POST", "/v1/missions", fields)

    def open_tree(self, tree_id: str, mission_id: str, root_actor: str) -> dict:
        return self._call(
            "POST", "/v1/trees",
            {"tree_id": tree_id, "mission_id": mission_id, "root_actor": root_actor},
        )

    def tree(self, tree_id: str) -> dict:
        return self._call("GET", f"/v1/trees/{tree_id}")

    def spawn(self, tree_id: str, actor: str, parent_id: str = "", grant: Optional[dict] = None) -> dict:
        return self._call(
            "POST", f"/v1/trees/{tree_id}/nodes",
            {"parent_id": parent_id, "actor": actor, "grant": grant or {}},
        )

    def revoke_tree(self, tree_id: str, reason: str = "") -> dict:
        return self._call("POST", f"/v1/trees/{tree_id}/revoke", {"reason": reason})

    def reserve(self, tree_id: str, amount_minor: int) -> dict:
        return self._call(
            "POST", f"/v1/trees/{tree_id}/reservations", {"amount_minor": amount_minor}
        )

    def settle(self, tree_id: str, handle: str, actual_minor: Optional[int] = None) -> dict:
        body = {} if actual_minor is None else {"actual_minor": actual_minor}
        return self._call("POST", f"/v1/trees/{tree_id}/reservations/{handle}/settle", body)

    def release(self, tree_id: str, handle: str) -> dict:
        return self._call("POST", f"/v1/trees/{tree_id}/reservations/{handle}/release")

    # -- task-tree tokens -------------------------------------------------

    def issue_root_token(self, tree_id: str, key) -> dict:
        """The root agent's token, bound to `key`."""
        from .dpop import key_thumbprint

        return self._call("POST", "/v1/token",
                          {"tree_id": tree_id, "jkt": key_thumbprint(key)})

    def exchange_token(self, parent_token: str, parent_key, *, actor: str,
                       grant: Optional[dict] = None, child_key=None) -> dict:
        """A narrower token for a sub-agent — the only path to a downstream one.

        Authorized by the parent token plus a proof of holding its key, not by
        a deployment credential: an agent process has the former and usually
        not the latter.
        """
        from .dpop import key_thumbprint, make_proof

        url = f"{self.base_url}/v1/token/exchange"
        return self._call("POST", "/v1/token/exchange", {
            "task_token": parent_token,
            "dpop_proof": make_proof(parent_key, method="POST", url=url,
                                     access_token=parent_token),
            "actor": actor,
            "grant": grant or {},
            "jkt": key_thumbprint(child_key or parent_key),
        })

    def for_task_token(self, task_token: str, key, tool_url: str = "") -> "RemotePolicyDecisionService":
        """A client presenting a specific node's token.

        Returned as a NEW client rather than by mutating this one, because a
        sub-agent's token must not silently become the parent's — they are
        different principals and sharing one mutable client is how they stop
        being.
        """
        return RemotePolicyDecisionService(
            self.base_url,
            credential=self.credential,
            subject_token=self.subject_token,
            subject_token_provider=self.subject_token_provider,
            task_token=task_token,
            dpop_key=key,
            tool_url=tool_url or self.tool_url,
            timeout=self.timeout,
        )

    def denylist(self) -> dict:
        return self._call("GET", "/v1/denylist")

    def merge_denylist(self, entries: dict[str, int]) -> dict:
        return self._call("POST", "/v1/denylist", {"entries": entries})

    def wait_until_ready(self, attempts: int = 40, delay: float = 0.25) -> dict:
        """Poll `/healthz` until it answers.

        Belongs in the client rather than in every caller's start-up script:
        a container that depends on this service needs the same loop, and a
        hand-rolled one usually ends up either too short for a cold start or
        silently infinite.
        """
        import time

        last: Exception | None = None
        for _ in range(attempts):
            try:
                return self.health()
            except ServiceError as exc:
                last = exc
                time.sleep(delay)
        raise ServiceError(f"{self.base_url} never became ready: {last}")
