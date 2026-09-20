# warrant-service — the enforcement plane over HTTP

`warrant/` is the engine: a library that decides. This is the socket it decides
through — a Policy Decision Service any agent runtime can call before it acts.

```bash
docker compose -f ../docker-compose.warrant.yml up --build -d
open http://localhost:8150/docs     # the API
open http://localhost:8140          # the Arcadia console, deciding through it
```

Or without Docker:

```bash
cd warrant-service
VIRTUAL_ENV=.venv uv venv && VIRTUAL_ENV=.venv uv pip install -r requirements-dev.txt
WARRANT_ACTION_REGISTRY_PATH=../warrant-demo/policy/action_registry.yaml \
  VIRTUAL_ENV=.venv .venv/bin/python -m warrantservice
```

## Why this is a separate package

The engine depends on `cryptography` and nothing else, because the spec
promises a resource server can verify a Mission offline with published keys and
an open verifier — and a verifier that first had to install a web framework
would satisfy neither the letter nor the spirit of that. So wire concerns live
here, and the engine stays embeddable in a process that has never heard of HTTP.

The same discipline runs one level deeper: **every submodule in
`warrantservice` is imported lazily.** The client ships into a customer's agent
process, where the promise is "under 50 lines to integrate", not "plus a
resolution argument with your existing HTTP library" — so importing
`RemotePolicyDecisionService` must cost nothing but the standard library.
`tests/test_client_is_stdlib_only.py` holds that line in a fresh interpreter,
after an eager `config` import (PyYAML) once killed the demo container on
startup.

## The API

Setup happens off the call path and is allowed to be ordinary. One route is on
the path and is not.

| | |
|---|---|
| `POST /v1/decisions` | **the hot path.** allow / deny / step_up |
| `POST /v1/missions` | turn one human approval into a signed Mission |
| `GET /v1/missions/{id}` · `POST .../revoke` · `POST /v1/missions/verify` | lifecycle |
| `GET /.well-known/jwks.json` | public keys, so anyone can verify a Mission offline |
| `POST /v1/agent-keys` · `GET /v1/agent-keys` | register an agent process's public key |
| `POST /v1/trees` · `GET /v1/trees/{id}` · `POST .../nodes` · `POST .../revoke` | the task tree |
| `POST /v1/trees/{id}/reservations` · `.../settle` · `.../release` | the budget ledger |
| `GET /v1/denylist` · `POST /v1/denylist` | revocation replication |
| `GET /v1/registry` · `GET /healthz` | what this deployment's vocabulary is |

Four decisions worth knowing before you integrate:

**A refusal is a 200 with a decision, not an error status.** A denied call is
the service working. A caller that distinguishes "denied" from "service broken"
by HTTP status will eventually get it wrong in the permissive direction — or
retry a denial as though it were a transport blip. 4xx means the request was
malformed, and the body names the field.

**Unknown fields are refused, never ignored.** A client that sends
`bypass_budget: true` has misunderstood something; dropping it silently turns a
loud integration bug into a quiet authorization gap.

**Deciding consumes no budget.** The PDS stays a pure function across the wire,
so a proposed policy can be replayed against recorded attestations. Whoever
executes the call takes the reservation — which is why `reserve` and `settle`
are separate routes rather than folded into the decision.

**Transport failure is not permission.** The client raises on every transport
error rather than returning a decision. Fail closed; the spec settles the same
open question the same way.

## Authentication

Two different questions, answered by two different parties. Conflating them is
how a system ends up with a control that looks like identity and is not.

**Who may call this service** — `auth.py`. Scoped service credentials, presented
as `Authorization: Bearer <client_id>.<secret>`. Scopes exist because the routes
are not equally dangerous: a gateway on the hot path needs `decide` and nothing
else, while minting consent is the highest privilege in the system and belongs
to a control plane that never touches a tool call. One credential for everything
would mean every gateway could issue Missions — the same hole with a password
on it.

```bash
python -m warrantservice.credentials scopes                    # what each opens
python -m warrantservice.credentials new gateway     --scopes decide,read --append-to credentials.yaml
```

The file stores SHA-256 of each secret and the secret is printed once, so a
leaked config is a list of hashes rather than of working keys. Plaintext
secrets are refused.

**A missing credentials file is a hard startup failure.** Not a degraded mode:
an unauthenticated PDS is an open door in front of the one component that can
*widen* permissions. Before this existed, four unauthenticated calls — register
your own signing key, mint a Mission naming any subject with any budget, open a
tree, ask — produced an `allow`. `WARRANT_ALLOW_UNAUTHENTICATED=1` still gets
you that, but you have to write it down, and `/healthz` reports it for as long
as it lasts.

**Who the end user is** — `oidc.py`. The subject used to be a string in the
request body, so the PDS's subject check read like an identity control and was
not one. Now, with an issuer configured, the caller presents the IdP's own
token and it is verified: signature against the published JWKS, plus issuer,
audience and expiry. `sub` from the verified claims is the subject.

The unverified path is then **closed, not deprecated**: a body carrying
`token_subject` is refused outright. Leaving the weak path open beside the
strong one protects nobody. The same applies to `POST /v1/missions` — with an
IdP configured, the approving user comes from their token, not from a name
someone typed.

Verification uses PyJWT rather than anything hand-rolled, and the algorithm
allow-list is asymmetric only: with HMAC the verification key is the signing
key, so anything able to verify could also mint. A symmetric or `none`
algorithm is refused at startup, not per token.

## Task-tree tokens

The third question, after "who is calling" and "who is the user": **which node
is this?**

An Action Attestation is signed by a registered agent key and *names* its node.
The signature binds the arguments honestly, but node identity was self-asserted
— so agent B, holding a perfectly valid key, could attest as a node belonging
to agent A and have every downstream control evaluate it against A's grant.

A task token says, signed by the control zone: this key holds this node, in
this tree, under this Mission, for this user, with this actor chain behind it.
Set `WARRANT_TASK_TOKENS=required` and the self-asserted path closes — `node_id`
and any subject field in a decision body are refused, because the token already
says both.

```
POST /v1/token           root token for a tree          (scope: token:issue)
POST /v1/token/exchange  a narrower one for a sub-agent (parent token + proof)
```

**Exchange is the only path to a downstream token**, and it does not
re-implement narrowing: it calls `TaskTree.spawn`, so the child's grant is the
engine's intersection and the depth cap is the engine's check. A second
implementation of "cannot widen" living beside the first is how the two
eventually disagree, and the one that is wrong is the one an attacker finds.

Exchange is deliberately **not** behind a service-credential scope. The parent
token plus proof of holding its key *is* the authorization — the OAuth token
exchange model (RFC 8693), and the right one here because an agent process
holds its task token, not a deployment credential.

**Proof of possession (DPoP, RFC 9449)** is what makes a stolen token useless.
The token carries `cnf.jkt`, the thumbprint of the holder's key; every
presentation needs a fresh proof signed by the matching private key. Verified
live: the agent holding its own key gets `allow`; a thief holding the same
token gets `401 proof_not_valid`.

DPoP rather than mTLS-bound tokens because it needs no TLS infrastructure at
the customer — and because the agent already holds an Ed25519 key for signing
attestations, so the same key can prove possession. That reuse buys a property
neither mechanism has alone: the service can check that the key which proved
possession is the key that signed the claim.

A proof is single-use within its window (`jti`), pinned to one method and URL
(`htm`/`htu`, so one captured at a harmless endpoint cannot be presented at a
dangerous one), and bound to one specific token (`ath`).

The actor chain is emitted as RFC 8693 `act`, nested innermost-first, so an
OAuth-aware consumer can already read it.

## Configuration

| | |
|---|---|
| `WARRANT_ACTION_REGISTRY_PATH` | the deployment's verb list (YAML or JSON) |
| `WARRANT_ISSUER` | the Mission Authority's name |
| `WARRANT_POLICY_VERSION` | stamped on every decision, so one can be replayed against the rules that were live |
| `WARRANT_AUTHORITY_KEY` | base64url 32-byte Ed25519 private key |
| `WARRANT_AUTHORITY_KEY_ID` · `WARRANT_HOST` · `WARRANT_PORT` | |
| `WARRANT_CREDENTIALS_PATH` | who may call this service, and with which scopes |
| `WARRANT_ALLOW_UNAUTHENTICATED` | `1` to run with no caller authentication — deliberate only |
| `WARRANT_OIDC_ISSUER` | the customer's IdP; setting it closes the unverified subject path |
| `WARRANT_OIDC_AUDIENCE` | strongly advised — a token for another app at the same issuer is still a valid token |
| `WARRANT_OIDC_JWKS_URL` | defaults to `<issuer>/.well-known/jwks.json` |
| `WARRANT_OIDC_ALGORITHMS` · `WARRANT_OIDC_LEEWAY` · `WARRANT_OIDC_CACHE_SECONDS` · `WARRANT_OIDC_REFRESH_COOLDOWN` | |
| `WARRANT_TASK_TOKENS` | `required` closes the self-asserted-node path |
| `WARRANT_TOKEN_KEY` · `WARRANT_TOKEN_TTL` · `WARRANT_TOKEN_AUDIENCE` | task-token signing; the key is separate from the Authority's on purpose |
| `WARRANT_DPOP_WINDOW` | proof acceptance window, seconds |

Two states the service announces at startup because both are legitimate and
both silently change what it does:

- **No registry configured.** It starts with an empty one, and since an
  unregistered action class fails closed, it denies every side-effect call and
  `GET /v1/registry` says why. Running and refusing is the honest posture for a
  service nobody has configured. A path that is *set but unreadable* is a hard
  startup failure instead — someone intended a boundary, and starting anyway
  would silently swap their policy for the empty one.
- **No authority key supplied.** One is generated, and Missions signed under it
  stop verifying at the next restart — fine for a demo, wrong for anything
  else, and said out loud rather than discovered later.

## The client

```python
from warrantservice import RemotePolicyDecisionService

pds = RemotePolicyDecisionService(
    "http://warrant-pds:8150",
    credential="gateway.<secret>",       # which component is calling
    subject_token=users_idp_token,       # on whose behalf
)
decision = pds.decide(request)           # same DecisionRequest, same Decision
```

A gateway serves many users, so it cannot hold one fixed subject token; pass
`subject_token_provider=` instead — a callable from the subject named on a
`DecisionRequest` to that user's token, cached per subject.

Standard library only. `decide()` takes and returns exactly what the in-process
engine does, so moving the PDS in or out of your own process is a one-line
change — which is how the client stays honest: `warrant-demo` runs both ways,
and `tests/test_demo_parity.py` asserts all twelve situations produce identical
decisions, reason codes and per-control results over a real socket.

## Tests

```bash
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q      # 57 tests
```

In `make test` and CI. The parity suite starts a real uvicorn server rather
than using FastAPI's `TestClient`, because half of what it checks is that the
wire format survives a round trip — an in-process client would pass even if the
codec were broken.

## What this is not, yet

**State is in-memory and dies with the process.** Missions, trees, ledgers and
the denylist are all held in RAM by design at this stage, not behind a store
interface that implies otherwise. A restart loses every live task, and two
replicas share nothing but whatever denylist you replicate between them.
`warrant/README.md` lists the Task Control Plane's persistence among the things
the spec's Phase 1 still needs, along with the gateway, the token service, the
consent screen and the evidence store.

**The DPoP replay cache is per process.** Two replicas do not share it, so a
proof accepted by one could be replayed against another within its window. The
fix is a shared store, not a bigger cache.

**Tokens cannot be revoked individually.** Revoking the tree stops every token
in it at the next decision, which is the guarantee that matters, but there is
no per-`jti` denylist and no refresh flow — a token simply expires.

**Step-up has no delivery.** `step_up` is returned with the delta that caused
it, and nothing carries it to a human, captures their answer, or resumes the
branch.

**Credentials have no rotation window or lockout.** Rotating means editing the
file and restarting; there is no rate limiting on failed attempts.
