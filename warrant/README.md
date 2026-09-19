# Warrant — the enforcement plane

Prefront's pitch is **authorize, judge, prove, from one trace**. This repo has
had the judging half for a while (`eval-engine`: the three check families, the
verdict contract, compliance mapping). `warrant/` is the authorizing half: the
component that decides, *before* a side-effect call happens, whether it is
permitted — and that the model cannot reach.

Status: **the decision core, offline and tested.** No service, no gateway, no
transport. See "What is not here yet" at the bottom.

## The one-paragraph version

A user approves a task once, on a consent screen. That approval is captured as
a signed **Mission**: the hash of their instruction, the action classes,
resources and counterparties they agreed to, a budget, a validity window, and
how deep sub-agents may go. Every side-effect call the agent then makes carries
a signed **Action Attestation** — what the model claims it is about to do, and
on what evidence. The **Policy Decision Service** checks the second against the
first and returns **allow / deny / step-up**. Nothing the model emits can widen
what the Mission permits.

## Why each piece is shaped the way it is

**`canonical.py` — hashing is the integrity boundary, not a formatting choice.**
The deployment splits so the control zone holds hashes and the data zone holds
payloads; the auditor's chain of evidence is the claim that those two match. So
two processes in two zones, possibly in two languages, must hash the same
logical object to the same bytes. Sorted keys, no NaN, `1.0` and `1` are one
value, sets are refused rather than ordered by guess, and every digest is
domain-separated by a type tag so an argument bag can never be presented as a
Mission.

**`signing.py` — Ed25519 only, and the algorithm field is checked, never
dispatched on.** Algorithm agility in a signed-token format is where the
alg-confusion bugs live. Signatures are detached, over canonical bytes, so a
verifier reconstructs what it should verify from the object it already holds —
there is no parse-before-verify step to attack. A published JWKS is enough to
verify offline, which is what the spec's air-gapped deployment and its
"open-source verifiers per language" both require.

**`tree.py` — narrowing is an intersection, not a check.** A child node's grant
*cannot* widen, because `Grant.narrow` computes an intersection: handing it an
action class the parent lacks drops that class rather than adding it. The
subtle line is what an *unconstrained* request means — it inherits the parent's
list rather than the world's, which is the only reading that cannot escalate.
Budget is the tree's, not the node's, so fanning out does not multiply the
ceiling; spending is reserve-then-settle, because a ceiling checked only at
decision time lets two concurrent branches each see the full remaining budget.

**`pds.py` — pure, exhaustive, fail-closed.** It reads state and mutates none,
which is what makes the spec's sandbox replay meaningful: the same inputs must
yield the same decision. Every check runs — no short-circuit on the first
failure — because the Narrow stage compares what was permitted with what was
exercised and needs the whole picture. A check that cannot see what it needs
returns `indeterminate`, and indeterminate counts as that check's violation.

**Step-up is not a soft deny.** A new counterparty or an over-budget call pauses
one branch and shows a human the specific delta under their original
instruction. Denying those outright is what teaches users to approve Missions
with an empty counterparty list, which removes the control entirely.

## What the tests are

`warrant/tests/` is organised by the attack each case stops, not by method, so
the question "what stops X?" is answerable by reading test names. It covers the
scenarios the spec calls out by name: replayed tokens, forwarded sub-agent
tokens, injected payment instructions, over-budget trees, superseded Missions,
and revocation reaching a replica that never saw the tree.

```bash
cd warrant
VIRTUAL_ENV=.venv uv venv && VIRTUAL_ENV=.venv uv pip install -r requirements-dev.txt
VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q      # 93 tests
```

It is also in `make test` and in CI.

## Two honest limits

**The Intent Binder is inside the blast radius.** It runs in the customer's
agent process, so an attacker who controls the agent controls its key. That is
not a flaw to fix here — it is *why* the PDS is a separate party, and why the
judgement plane recomputes provenance from shipped content instead of trusting
the `origin` labels an attestation carries. The spec lists this as a standing
risk, and the injection tripwire's dependence on honest labelling is audited
after the fact by `evidence_mismatch`, not prevented on the path.

**An unknown action class fails closed, and that will bite.** A tool that ships
without the registry being updated gets denied. The safe-looking alternative —
assume an unregistered verb is a harmless read — is exactly how a new
destructive tool ends up governed by nothing. The fix is the lifecycle loop
re-deriving the boundary when the tool surface changes, which is a deploy-time
problem with a deploy-time fix.

## Domain independence

`warrant/` names no deployment, and `tests/test_domain_independence.py`
enforces it the way `eval-engine`'s guard does — deployment names, anywhere in
the package, comments included. A customer's vocabulary reaches the enforcement
plane through the Mission and the action registry, never through code. The
registry ships empty on purpose.

## What is not here yet

The spec's Phase 1 is a shipping pilot; this is its decision core. Not built:

| Component | Spec section |
|---|---|
| Enforcement Gateway (proxy in front of MCP/HTTP tools) | "Gateway mode" — the zero-tool-change integration |
| Token Service (OAuth 2.1, stamps `mission`/`tree_id`/`node_id`) | "Task-tree tokens" |
| Consent Component (the actual approval screen) | "Signed Missions" |
| Evidence Store (payloads keyed on `node_id` + `args_hash`) | data-zone half of the chain |
| Step-up delivery (Slack, Teams, mobile, email) | "Step-up mid-task" |
| Runtime adapters (Claude Agent SDK, LangGraph, …) | "Runtime adapters" |
| Denylist propagation (the 5-second guarantee) | `TreeStore` has the replicable shape; distribution is not written |

The seam to the existing judgement plane is `CheckResult`, which deliberately
speaks the same `satisfied | violated | indeterminate` language as
`evalengine.contract.Verdict`, with `on_violation` mapping onto that contract's
`block` / `approval_required`. The spec plans to promote proven integrity
checks inline as a PDS plugin; that alignment is what makes it a registration
rather than a rewrite.
