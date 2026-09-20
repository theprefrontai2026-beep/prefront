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

## Configuration

| | |
|---|---|
| `WARRANT_ACTION_REGISTRY_PATH` | the deployment's verb list (YAML or JSON) |
| `WARRANT_ISSUER` | the Mission Authority's name |
| `WARRANT_POLICY_VERSION` | stamped on every decision, so one can be replayed against the rules that were live |
| `WARRANT_AUTHORITY_KEY` | base64url 32-byte Ed25519 private key |
| `WARRANT_AUTHORITY_KEY_ID` · `WARRANT_HOST` · `WARRANT_PORT` | |

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

pds = RemotePolicyDecisionService("http://warrant-pds:8150")
decision = pds.decide(request)          # same DecisionRequest, same Decision
```

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

**There is no authentication on these routes.** The PDS trusts its caller to be
the gateway. That is the right shape — the spec's identity comes from the
customer's IdP, and the token service is a separate component — but it means
this service must not be exposed beyond the call path it serves.
