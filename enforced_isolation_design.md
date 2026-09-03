# Enforced per-application isolation — design proposal

**Status: PROPOSED, not built.** Successor to `application_isolation_design.md`,
which is built (Phases 1-3 + scoped deletes). That design made isolation
*possible*; this one makes it *hold*.

Written in answer to "can we maintain per-app isolation with this design
everywhere?" — no, and the reasons are structural rather than unfinished work.

---

## 1. Soft vs enforced

What exists today is a **convention**: every read *can* take an application
scope, and the callers currently pass one. Nothing makes them.

```python
# Both compile. Both run. One returns every application's findings.
store.list_findings(app=app_id)
store.list_findings()
```

That is not a boundary — it is a habit, and habits are exactly what a new
endpoint, a refactor, or a rushed fix breaks. It has already happened twice in
this codebase, both measured:

- **Every `/eval/*` endpoint** was deployment-wide until Phase 2, while the UI
  that called them was labelled with one application.
- **`?project=` was accepted and silently broken** on `/oob/sessions` and
  `/oob/traces` from the day the parameter was added — the alias trap made it
  raise, so nothing had ever exercised it.

**Enforced** means an unscoped read is not something a developer can write by
accident. Two properties:

1. **One canonical key.** An application has exactly one identifier, and every
   other key derives from it.
2. **Unscoped access must be spelled out.** The default path carries the scope;
   reading across applications requires naming that intent, in a form that is
   greppable, testable and logged.

---

## 2. Why the current design cannot get there

**Three keys, never reconciled.** `app_id` (spans, `eval_*`, skill-builder),
`demo` (`decision_*`, `severity_rule`), `datasource_id` (`query_templates`,
`functions`, `datasources`). They agree today only because each bundled demo
uses matching strings. `application_isolation_design.md` §7.1 settled that an
application has MANY datasources, so `app_id` and `datasource_id` are
guaranteed to diverge.

The scoped-delete work made this visible in code rather than in prose — the UI
has to pass all three, because each store is scoped by a different one:

```ts
fetch(`/oob/phoenix?project=${project}`)          // Phoenix project
fetch(`/eval/verdicts?app=${appId}`)              // app_id
fetch(`/api/decisions?demo=${appId}`)             // demo
```

**And the scope is optional everywhere.** Adding a fourth store, or a
fifteenth endpoint, reintroduces the gap silently.

---

## 3. The good news, measured

Enforcement is a smaller change than it sounds, because the funnels already
exist:

| service | data access | funnel? | sites |
|---|---|---|---|
| eval-engine | `ch.rows()` / `ch.one()` | **yes** | 24 reads through it; 4 direct `client()` calls, all commands (TRUNCATE/ALTER/INSERT), no reads |
| oob-ingest | `ch.rows()` / `ch.one()` | **yes** | same shape, 4 direct commands |
| api-server | Drizzle, called directly | **no** | 7 sites across `decisions.ts` (5), `audit.ts` (1), `settings.ts` (1) |
| skill-builder | `Store` methods over SQLAlchemy | partial | already a data-access layer; scoping added in Phase 3b |

So two of the four services already route every read through one function. The
work is to give that function teeth, and to add a funnel to the one service
without one — not to rewrite data access.

---

## 4. The design

### 4.1 One canonical key

`app_id` is the identifier. Everything else derives:

| key | becomes |
|---|---|
| `demo` | **renamed** to `app_id`. It was always an application label — `application_isolation_design.md` §7.1 established there is nothing to merge |
| Phoenix `project` | an **attribute** of an application, mapped to `app_id` at ingest (already built: `OOB_PROJECT_ALIASES`, `EVAL_PROJECT_APP_MAP`) |
| `datasource_id` | stays, as a **child**. An application has many; the registry is the join. No table keyed by datasource changes its key |

The application registry (`applications.yaml`, built in Phase 3) becomes the
single source of that mapping, rather than each service inferring it.

### 4.2 A scope you cannot forget

Replace module-level store functions with a **handle that carries the scope**:

```python
# The only way to get a reader.
s = store.for_app(app_id)        # every read through `s` is scoped
s.list_findings(since=3600)      # no `app` parameter exists to omit

# Reading across applications is a different, named object.
s = store.across_applications(reason="operator ingestion health")
```

Three properties this buys that an optional parameter cannot:

- **The scoped call has no unscoped form.** `list_findings()` on a scoped
  handle cannot return another application's rows, because the predicate is
  applied by the handle, not by the caller remembering an argument.
- **Cross-application access is greppable.** `across_applications(` is one
  token to search for, review, and count — the same property that makes
  `include_disabled=true` reviewable today.
- **It is auditable.** The handle logs the reason at construction, so
  "who read across applications, and why" is answerable from the log rather
  than by reading code.

### 4.3 A guard that fails the build

The property "no read bypasses the funnel" is not expressible in Python's type
system, but this repo already enforces an equally untypeable invariant with a
static test: `eval-engine/tests/test_domain_independence.py` parses the source
and fails if a domain noun reaches an executable token. It carries **positive
controls** — tests that it FIRES on an injected violation — because a guard
with no proof it can fail is the recurring bug shape this codebase has hit
repeatedly.

The isolation guard is the same shape, and should be built the same way:

1. every read reaches ClickHouse through `ch.rows()`/`ch.one()` — a direct
   `client().query(` outside the funnel fails;
2. `rows()` requires a scope object, so a query string that names a scoped
   table without a bound predicate fails;
3. `across_applications(` appears only in an allowlisted set of call sites,
   each with a recorded reason;
4. positive controls proving each of the three fires.

That is where the teeth are. Without (4) the guard is decoration.

### 4.4 Which tables are scoped

Not all of them, and saying which is part of the design rather than an
afterthought:

| scoped by `app_id` | deliberately global |
|---|---|
| `spans`, `eval_verdicts`, `eval_conformance_tags`, `eval_evaluated_sessions` | `ingest_state` (per-project already; poll bookkeeping, not evidence) |
| `decision_trace`, `decision_stat`, `decision_agent`, `decision_policy`, `decision_intent`, `severity_rule` | `eval_settings` (deployment-wide default the registry overrides) |
| `source_documents`, `skill_versions` (+ the nine tables that reach them) | framework packs (shipped, not deployment data) |
| `rule_audit_log` — **currently global, should be scoped** | |

A table's presence in the left column is what the guard checks against.

---

## 5. What this still does not give you

Stated plainly, because the gap between "enforced in the application layer" and
"isolated" is where this kind of design usually oversells itself.

- **One shared database.** `spans` and `eval_*` remain one ClickHouse database
  with one credential. A SQL injection, a bug in the predicate builder, or
  anyone with database access reads everything. Application-layer enforcement
  is a guarantee about *this codebase*, not about the data.
- **Retention stays global** unless separately addressed (`OOB_RETENTION_DAYS`,
  `EVAL_RETENTION_DAYS` are single ints applied as table TTLs). One app cannot
  keep 90 days while another keeps 7 — `TODO` entry 18.
- **No authentication.** There is no auth layer at all (`TODO` entry 14), so
  "this request may only see application X" has no principal to bind to. Until
  that exists, the scope is chosen by the caller, and enforcement means "cannot
  do it *by accident*", not "cannot do it".

**If you need true tenancy, the answer is a database per application** — one
ClickHouse database and one credential each. That gets isolation from the
storage layer instead of from a convention, and it kills the cross-application
views (fleet health, "every app's findings") that a single database makes
trivial. It is a different product shape, and it should be chosen deliberately
rather than arrived at.

---

## 6. Migration

Each step is independently shippable and leaves the system working. No step
requires the next.

**A. Reconcile the keys.** Rename `demo` → `app_id` in the `decision_*` and
`severity_rule` tables, and make the registry the one place project → app is
resolved. Pure rename; §7.1 established there is nothing to merge.

**B. Add the api-server funnel.** Seven call sites, no funnel today. Smallest
service, and doing it first proves the shape before touching the two with 24+
sites.

**C. Introduce the handle** alongside the current functions, migrating callers
service by service. The old functions stay until the last caller moves, so no
big-bang.

**D. Turn on the guard.** Only after C is complete for a service — a guard
that fails on day one gets an allowlist added to it, which is how a guard dies.

**E. Delete the optional-`app` parameters.** The point of no return: after
this, an unscoped read is unwritable rather than merely discouraged.

**F. Scope retention** and `rule_audit_log`. Independent of A-E.

---

## 7. What it costs

- **Every read site changes** — ~31 in the Python services plus 7 in the
  api-server. Mechanical, but not small.
- **Cross-application surfaces need explicit rewriting.** Ingestion health, the
  Phoenix status block and any future fleet view are legitimately
  cross-application and must move to `across_applications()` with a reason.
- **A guard is a maintenance burden.** It will block a legitimate change one
  day, and the response has to be "argue the exemption in the diff", not
  "add it to the allowlist" — the discipline `test_domain_independence.py`
  already documents for itself.

The honest summary: this is a week-shaped change, not a day-shaped one, and it
buys "an unscoped read is a build failure" rather than "an unscoped read is a
bug someone will notice later". Whether that trade is worth it depends on
whether Prefront is demoing several applications or hosting them — a question
worth answering before A rather than during D.
