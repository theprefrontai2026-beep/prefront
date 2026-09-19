# Arcadia Capital — the treasury demo

An investor- and executive-facing demonstration of Prefront's enforcement
plane. One agent, one night's work, run twice: once the way almost every agent
in production runs today, and once through Warrant.

```bash
cd warrant-demo
python3 server.py                     # -> http://localhost:8140
python3 server.py --check             # one line, non-zero on drift; no browser
```

Only dependency is `cryptography`, which the engine needs to sign anything. If
you have the engine's venv it is already there:
`../warrant/.venv/bin/python server.py`.

## The situation

Arcadia Capital's Treasury Operations agent settles supplier invoices
overnight, unattended. An operator approved the task once, in one sentence:

> Settle the March supplier invoices overnight, up to $250,000 in total, to our
> established suppliers. Flag anything unusual for me in the morning.

That sentence becomes a signed Mission — four action classes, six invoices,
three approved suppliers, a $250,000 ceiling, a five-hour window, one tier of
sub-agents. Everything the demo shows is that Mission being enforced.

## Twelve situations

| | Situation | What it answers |
|---|---|---|
| BASE-01 | Overnight settlement, nothing unusual | Does this just block my agent? |
| INJ-01 | A supplier document carries new banking details | What stops a document redirecting my money? |
| INJ-02 | An amendment inside the document inflates the amount | What if the attacker stays inside my policy? |
| BND-01 | A legitimate supplier nobody approved yet | Where do approvals show up? |
| BND-02 | The batch runs past its spending ceiling | Can an agent spend more than I agreed to? |
| BND-03 | The agent broadens its own query | What about over-broad retrieval? |
| DEL-01 | A read-only sub-agent reaches for the wire | Can a sub-agent escalate? |
| INT-01 | A proxy rewrites the destination account in flight | What if the compromise is below my agent? |
| INT-02 | A captured settlement is presented twice | Can a leaked token be replayed? |
| CTL-01 | The operator revokes the task mid-run | Who presses stop? |
| CTL-02 | A scheduled run fires outside its window | Can it run at 3 a.m. — and only then? |
| CTL-03 | Another operator's consent is presented | Is this a second identity store? |

Across the twelve: **$180,350 stopped**, four money-losing situations become
zero, and two calls are sent to the operator rather than refused.

## Presenting it

Fifteen minutes, in this order.

**Open with BASE-01.** The agent settles two invoices and every control
passes. Nothing is slowed, nothing is blocked. An audience that has not seen
the allow path will not believe the deny path, and the first objection in the
room is always "so it breaks my agent".

**Then INJ-02, which is the whole pitch in one screen.** A supplier's own
invoice PDF carries an "amendment" inflating a $9,750 invoice to $105,800. The
agent reads it, believes it, and proposes the payment — which is what a
competent model does. Now look at the control panel: identity passes, scope
passes, the counterparty is approved, the amount is inside budget, the token is
genuine. Eleven of twelve controls are green. The only thing wrong is that the
number came from a document, and that is the one thing an identity vendor, an
API gateway or an authorization engine is not positioned to see.

**Then BND-01, so nobody leaves thinking this is a wall.** A real new supplier
gets *held for the operator*, not denied, with the single thing that changed
named under the original instruction. That distinction is what stops customers
approving Missions so wide they never have to be asked.

**Close on CTL-01.** The operator presses stop mid-run and the rest of the
batch dies at the next decision — including on a replica that never saw the
task, because what replicates is the denylist rather than the task.

If there is time, DEL-01 and INT-01 are the two a security reviewer will ask
for unprompted.

## What is real here, and what is staged

Being precise about this is worth more than the demo:

- **The decisions are real.** Every outcome, reason code and control result on
  screen is the output of `warrant/`, computed at page load. Nothing is
  narrated over a pre-baked answer. `test_demo.py` asserts the documented
  outcome of all twelve, so the engine cannot change without the demo failing
  rather than lying.
- **The agent is scripted, deliberately.** What is being demonstrated is the
  enforcement plane, which is deterministic by construction — the spec's thesis
  is that the runtime never asks a model for permission. A live model would add
  variance to the one part of the system designed to have none, and produce a
  demo that behaves differently on stage than in rehearsal. The scripted
  behaviour is not a caricature: on a poisoned invoice the agent does what a
  competent model does, which is believe the document.
- **The suppliers, invoices and ledger are fictional.** No system of record is
  touched; `world.py` is the whole universe.
- **The clock is fixed.** Two runs a week apart are byte-identical.

## Why it is a separate directory

`warrant/` names no application — no supplier, no invoice, no currency, no
threshold — and `warrant/tests/test_domain_independence.py` enforces that on
every line, including comments. All of Arcadia's vocabulary lives here:

| File | What it holds |
|---|---|
| `world.py` | the action-class registry, suppliers, invoices, the ledger |
| `deployment.py` | the whole integration: ~60 lines wiring vocabulary into the engine |
| `agent.py` | the agent, and the two lanes it runs in |
| `scenarios.py` | the twelve situations |
| `runner.py` | runs both lanes, computes the business outcome |
| `server.py` | stdlib HTTP server; runs the catalogue and serves the console |
| `console.html` | the page — no build step, no framework |

`deployment.py` is the file worth showing an engineer who asks what
integration costs. It imports the engine, hands it one consent screen's worth
of fields and an action list, and gets back a decision service. It does not
subclass, patch or extend anything — and `test_demo.py` asserts that, so the
claim that the same engine governs a different application unchanged stays
true.

## Tests

```bash
python3 -m pytest test_demo.py -q     # 35 tests, offline
```

Also in `make test` from the repo root. They cover the documented outcome of
each situation, that both lanes run identical calls (so the contrast is
attributable to the control and not to the harness), that the demo touches only
the engine's public surface, and that the page and the server agree on the
payload shape in both directions.
