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
fail closed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

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

    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- transport --------------------------------------------------------

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
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

    def decide(self, request: DecisionRequest) -> Decision:
        """Identical in signature and return type to the embedded engine."""
        signed = request.signed
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
                "node_id": request.node_id,
                "now": request.now,
                "token_subject": request.token_subject,
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
