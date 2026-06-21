# Prefront AI — UI ↔ Backend Integration Guide

## 1. Architecture Overview

```
Browser
  │
  │  (all traffic via shared reverse proxy on :80)
  │
  ├─── /          →  Vite dev server  →  React UI (prefront-app)
  ├─── /api/*     →  Express server   →  api-server  (Node.js, :8080)
  └─── /design/*  →  FastAPI server   →  design-backend (Python, external)
```

There are **two distinct backend services** the UI talks to:

| Service | Base path | Language | Status |
|---|---|---|---|
| **API Server** | `/api` | Node.js / Express | ✅ Running |
| **Design Backend** | `/design` | Python / FastAPI | ⚠️ Must be connected |

The proxy routes traffic by path prefix — no CORS headers or base-URL changes are needed in the UI code. All `fetch()` calls in `src/api.ts` use relative URLs and will work once both backends are reachable.

---

## 2. What Is Already Connected

### 2.1 Health Check
```
GET /api/healthz
→ { status: "ok" }
```
Used to verify the API server is alive.

### 2.2 Audit Log — Read
```
GET /api/audit?documentId={id}
→ AuditEntry[]
```
Fetches up to 500 audit entries for a document from the `rule_audit_log` Postgres table. Called by `fetchAuditLog()` in `src/api.ts` and displayed in the Policy Studio review history panel.

### 2.3 Audit Log — Write
```
POST /api/audit
Body: { documentId, ruleKey, action, reviewerName, reviewerColor?, before?, after?, note? }
→ { id }
```
Creates a single audit entry. Validated with the `insertAuditSchema` Zod schema generated from the Drizzle table definition.

### 2.4 WebSocket — Collaborative Review
```
WS /api/ws/review
```
Real-time presence and rule-status sync between all open browser sessions. Managed by `useReviewSync.ts` on the client and `reviewHub.ts` on the server.

Full message protocol → see [Section 5](#5-websocket-protocol).

---

## 3. What Needs to Be Connected (Design Backend)

Every other call in `src/api.ts` targets `/design/*`. These are the endpoints the FastAPI backend must implement.

### 3.1 Document Management

#### List documents
```
GET /design/skills/documents
→ Document[]
```

#### Upload document (file or plain text)
```
POST /design/skills/documents/upload

# Option A — multipart (PDF / Word / plain-text file)
Content-Type: multipart/form-data
Fields: file (File), domain (string), version (string)

# Option B — JSON (pasted DDL / BRD text)
Content-Type: application/json
Body: { text: string, file_name: string, domain: string, version: string }

→ Document
```

#### Delete document
```
DELETE /design/skills/documents/{documentId}
→ { ok: true }
```

#### Profile document (domain + structure analysis)
```
POST /design/skills/documents/{documentId}/profile
Body: { pack: string | null, provider: string | null }
→ DocumentProfile
```

#### Classify clauses
```
POST /design/skills/documents/{documentId}/classify-clauses
Body: { provider: string | null }
→ ClauseClassification[]
```

#### Extract policy atoms
```
POST /design/skills/documents/{documentId}/extract-policy-atoms
Body: { provider: string | null }
→ PolicyAtom[]
```

#### Validate document
```
POST /design/skills/documents/{documentId}/validate
Body: { pack: string | null, declared_params: string[], metrics: string[] }
→ ValidationResult
```

#### List unresolved items
```
GET /design/skills/documents/{documentId}/unresolved-items
→ UnresolvedItem[]
```

#### Get clause ledger
```
GET /design/skills/documents/{documentId}/clause-ledger
→ ClauseLedger
```

#### List policy atoms (GET variant)
```
GET /design/skills/documents/{documentId}/policy-atoms
→ PolicyAtom[]
```

---

### 3.2 Rule Extraction & Review

#### Extract rules from a document
```
POST /design/skills/documents/{documentId}/extract-rules
Body: {
  provider: string | null,
  domain: string | null,
  known_intents: string[],
  known_fields: string[],
  known_roles: string[]
}
→ CandidateRule[]
```

#### List all candidate rules
```
GET /design/skills/candidate-rules
→ CandidateRule[]
```

#### List candidate rules for a document
```
GET /design/skills/candidate-rules?document_id={id}
→ CandidateRule[]
```

#### Edit a candidate rule
```
PATCH /design/skills/candidate-rules/{candidateRuleId}
Body: { rule: Partial<CandidateRule> }
→ CandidateRule
```

#### Approve a candidate rule
```
POST /design/skills/candidate-rules/{candidateRuleId}/approve
Body: { approved_by: string, version: string }
→ { ok: true, rule: ApprovedRule }
```

#### Reject a candidate rule
```
POST /design/skills/candidate-rules/{candidateRuleId}/reject
Body: { rejected_by: string, reason: string }
→ { ok: true }
```

---

### 3.3 Semantic Layer

#### Parse SQL/DDL schema into catalog
```
POST /design/semantic/catalog/parse
Body: { ddl: string, datasource_id: string }
→ Catalog
```

#### Introspect live database
```
POST /design/semantic/catalog/introspect
Body: { dsn: string, datasource_id: string, schema: string }
→ Catalog
```

#### Build governed SQL interfaces
```
POST /design/semantic/build
Body: {
  rules: ApprovedRule[],
  ddl: string,
  dsn: string | null,
  domain: string,
  datasource_id: string,
  intents: string[],
  metrics: Record<string, string>,
  caller_context: Record<string, string>,
  model_id: string
}
→ SemanticModel
```

#### Import dbt model
```
POST /design/semantic/import/dbt
Body: {
  dbt_model: object,
  overlay: object | null,
  ddl: string,
  dsn: string | null,
  domain: string,
  model_id: string,
  datasource_id: string
}
→ SemanticModel
```

#### List query templates
```
GET /design/semantic/templates?semantic_model_id={id}
→ QueryTemplate[]
```

#### Approve / reject a template
```
POST /design/semantic/templates/{templateId}/approve
POST /design/semantic/templates/{templateId}/reject
→ { ok: true }
```

#### Publish templates
```
POST /design/semantic/publish
Body: { semantic_model_id: string | null }
→ PublishResult
```

#### Publish policy
```
POST /design/semantic/publish-policy
Body: {
  rules: ApprovedRule[],
  ddl: string,
  dsn: string | null,
  domain: string,
  datasource_id: string,
  metrics: Record<string, string>
}
→ PublishResult
```

---

### 3.4 Domain Packs

```
GET /design/skills/domain-packs
→ DomainPack[]
```

#### Publish a skill
```
POST /design/skills/{skillId}/publish
Body: { document_id: string, name: string, domain: string }
→ PublishedSkill
```

#### Resolve an unresolved item
```
POST /design/skills/unresolved-items/{unresolvedId}/resolve
Body: { status: string, resolved_by: string, notes: string | null }
→ { ok: true }
```

---

## 4. Database Schema

Managed with **Drizzle ORM** (`lib/db`). The only table currently used by the API server:

```sql
CREATE TABLE rule_audit_log (
  id             SERIAL PRIMARY KEY,
  document_id    VARCHAR(128)  NOT NULL,
  rule_key       VARCHAR(256)  NOT NULL,
  action         VARCHAR(32)   NOT NULL,  -- "approved" | "rejected" | "extracted"
  reviewer_name  VARCHAR(64)   NOT NULL,
  reviewer_color VARCHAR(16),
  before         JSONB,
  after          JSONB,
  note           TEXT,
  created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
```

**Drizzle commands:**
```bash
# Push schema changes to the dev database
pnpm --filter @workspace/db run push

# The DATABASE_URL env var must be set (Postgres connection string)
```

---

## 5. WebSocket Protocol

**Endpoint:** `wss://<host>/api/ws/review`

Connection is established automatically by `useReviewSync.ts` on page load with exponential-backoff reconnect (1.5 s → 30 s cap).

### Messages — Server → Client

| Type | When | Payload |
|---|---|---|
| `hello` | On connect | `{ type, id, name, color }` — assigns reviewer identity |
| `presence` | Any roster change | `{ type, reviewers: Reviewer[] }` — full snapshot of all connected reviewers |
| `rule_status` | Another reviewer approves/rejects | `{ type, ruleId, status, by, color, documentId }` |

### Messages — Client → Server

| Type | Purpose | Required fields |
|---|---|---|
| `identify` | Set display name | `{ type, name }` |
| `focus` | Highlight which rule is being reviewed | `{ type, ruleId: string \| null }` |
| `rule_status` | Broadcast an approval or rejection | `{ type, ruleId, status: "approved"\|"rejected", documentId }` |

When the server receives `rule_status` it:
1. Relays it to every other connected reviewer.
2. Persists a row to `rule_audit_log` (best-effort — never blocks the relay).

### `Reviewer` object shape
```ts
interface Reviewer {
  id:            string;       // server-assigned session ID
  name:          string;       // display name
  color:         string;       // hex colour for avatar
  focusedRuleId: string | null;
}
```

---

## 6. How to Add a New API Endpoint (Step-by-Step)

This project follows a **contract-first** approach: OpenAPI spec → codegen → implementation.

### Step 1 — Define the endpoint in the OpenAPI spec
```yaml
# lib/api-spec/openapi.yaml
paths:
  /rules:
    get:
      operationId: listRules
      tags: [rules]
      summary: List approved rules
      parameters:
        - in: query
          name: documentId
          schema: { type: string }
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/Rule"
```

### Step 2 — Run codegen
```bash
pnpm --filter @workspace/api-spec run codegen
```
This generates:
- **React Query hooks** (`lib/api-spec/src/generated/`) — use in the frontend instead of raw `fetch()`
- **Zod schemas** — use on the server for request/response validation

### Step 3 — Implement the route in the API server
```ts
// artifacts/api-server/src/routes/rules.ts
import { Router } from "express";
import { db } from "../lib/db";

const router = Router();

router.get("/rules", async (req, res) => {
  const { documentId } = req.query;
  const rows = await db.select()...
  res.json(rows);
});

export default router;
```

```ts
// artifacts/api-server/src/routes/index.ts
import rulesRouter from "./rules";
router.use(rulesRouter);
```

### Step 4 — Use the generated hook in the UI
```tsx
import { useListRules } from "@workspace/api-spec";

function PolicyPanel({ documentId }: { documentId: string }) {
  const { data: rules, isLoading } = useListRules({ documentId });
  ...
}
```

---

## 7. Per-Tab Integration Checklist

| Tab | Frontend file | API calls needed | Status |
|---|---|---|---|
| **Data Connector** | `DataConnector.tsx` | `POST /design/semantic/catalog/parse`, `POST /design/semantic/catalog/introspect` | ⚠️ Design backend |
| **Data Graph** | `DataGraph.tsx` | Reads `schema` from `localStorage` (populated by Data Connector) | ✅ No server call |
| **Business Graph** | `BusinessGraph.tsx` | Static stub data (SecureBank BRD) | ✅ No server call |
| **Policy Studio** | `PolicyStudio.tsx` | Full `/design/skills/*` suite | ⚠️ Design backend |
| **Semantic Layer** | `Semantic.tsx` | `/design/semantic/build`, `/design/semantic/templates/*` | ⚠️ Design backend |
| **Runtime Diff** | `RuntimeDiff.tsx` | `/design/semantic/publish-policy` | ⚠️ Design backend |
| **Audit Log** | `PolicyStudio.tsx` | `GET /api/audit?documentId=` | ✅ Connected |
| **Live Presence** | `useReviewSync.ts` | `WS /api/ws/review` | ✅ Connected |

---

## 8. Connecting the Design Backend

### Option A — Run locally alongside the API server

Add a workflow entry pointing at the FastAPI process on an unused port, then add a route in `artifact.toml`:

```toml
# artifacts/design-backend/.replit-artifact/artifact.toml
[[services]]
localPort = 8081
name = "Design Backend"
paths = ["/design"]
```

The shared proxy will then forward `/design/*` to port 8081 — no changes to the React code required.

### Option B — Remote/hosted FastAPI

Set `DESIGN_API_URL` as an environment secret, then add a proxy route in the Express server:

```ts
// artifacts/api-server/src/routes/designProxy.ts
import { createProxyMiddleware } from "http-proxy-middleware";

export const designProxy = createProxyMiddleware({
  target: process.env.DESIGN_API_URL,
  changeOrigin: true,
  pathRewrite: { "^/api/design": "/design" },
});
```

Then mount it in `app.ts`:
```ts
app.use("/api/design", designProxy);
```

And update all fetch paths in `src/api.ts` from `/design/` → `/api/design/`.

---

## 9. Environment Variables

| Variable | Where set | Purpose |
|---|---|---|
| `DATABASE_URL` | Replit secret | Postgres connection string for API server |
| `SESSION_SECRET` | Replit secret | Express session signing |
| `PORT` | Injected by workflow | Listening port for each service |
| `DESIGN_API_URL` | Replit secret *(if Option B)* | Remote FastAPI base URL |

Set secrets via:
```bash
# Never commit secrets to the repo — use Replit Secrets panel
# or the environment-secrets skill
```

---

## 10. Quick Reference

```bash
# Start all services
# (handled by Replit workflows — do not run pnpm dev at root)

# Typecheck the UI
pnpm --filter @workspace/prefront-app run typecheck

# Typecheck the API server
pnpm --filter @workspace/api-server run typecheck

# Full typecheck across all packages
pnpm run typecheck

# Regenerate API hooks + Zod schemas from OpenAPI spec
pnpm --filter @workspace/api-spec run codegen

# Push DB schema changes (dev only)
pnpm --filter @workspace/db run push

# Test the health endpoint
curl http://localhost:80/api/healthz

# Test the audit endpoint
curl "http://localhost:80/api/audit?documentId=test-doc"
```
