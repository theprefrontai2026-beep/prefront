---
name: Prefront AI UI
description: Port of prefront-ui.tar.gz into artifacts/prefront-app — architecture decisions and sharp edges.
---

## Stack
- React + Vite, TypeScript, NO shadcn — custom CSS system in `src/index.css`
- Tailwind installed but only used via `@import "tailwindcss"` — actual styling uses CSS custom properties
- `reactflow` + `dagre` installed for ERD in SchemaDiagram.tsx
- `ws` + `@types/ws` installed in api-server for WebSocket collaboration hub

## Proxy (vite.config.ts)
- `/api/ws` → `ws://localhost:8080` (ws: true flag required for Vite WS proxy)
- `/api` → `http://localhost:8080` (api-server express)
- `/design/semantic` → `VITE_SEMANTIC_TARGET` (default `http://localhost:8010`)
- `/design` → `VITE_API_TARGET` (default `http://localhost:8000`)
- ECONNREFUSED on /design* proxy paths is expected when FastAPI backends are not running locally

## WebSocket collaboration
- Server: `artifacts/api-server/src/lib/reviewHub.ts` — hub attached to HTTP server via `noServer: true`
- WS endpoint: `/api/ws/review` — intercepted in `server.on("upgrade", ...)` in `index.ts`
- Protocol: hello/identify/focus/presence/rule_status messages
- Client hook: `src/hooks/useReviewSync.ts` — auto-reconnects with exponential backoff
- Presence shown in header (pulsing dot + reviewer avatars) and policy studio sidebar
- Rule focus (hover) broadcasts to co-reviewers who's viewing which rule
- Approve/reject broadcasts instantly to all connected reviewers
- Toast notifications appear bottom-right for remote approval/rejection events
- api-server artifact.toml paths: ["/api", "/ws"]

**Why:** noServer pattern (not { server }) avoids port conflicts and lets Express own the HTTP server while WS upgrades are intercepted before they reach Express routes.

## Design tokens (src/index.css)
Warm Japandi paper palette:
- `--paper: #f5f2ec`, `--card: #fdfcf8`, `--field: #f0ebe1`, `--line: #e4ddd1`
- `--ink: #1e1c19`, `--sage: #4a6741`, `--clay: #7d5a38`, `--ochre: #8a6420`, `--terracotta: #8f4a38`
- All tint variants: `--sage-tint`, `--clay-tint`, `--ochre-tint`, `--terracotta-tint`
- Light mode only — no dark mode

## Component map
- `App.tsx` — 4-tab pipeline nav; shared state (rules, domain, schema, metricsText, callerScopeText, intents); localStorage for schema (`prefront.schema`) and intents (`prefront.intents`); useReviewSync wired here
- `DataConnector.tsx` — dsn/ddl/catalog modes; shows SchemaDiagram ERD on success
- `PolicyStudio.tsx` — sidebar doc explorer + co-reviewer sidebar section; upload/extract/classify/atoms/validate/unresolved sub-tabs; RuleCard review with focus/broadcast wired
- `Semantic.tsx` — build/dbt/templates/publish sub-tabs; TemplateCard inline component
- `RuntimeDiff.tsx` — loads test scenarios from `${server}/api/scenarios`; diff view governed vs ungoverned
- `RuleCard.tsx` — accepts focusers[], onMouseEnter/Leave for presence; shows focus chips above card
- `ClauseLedger.tsx`, `ValidationReport.tsx`, `UnresolvedItems.tsx`, `SchemaDiagram.tsx` — leaf components

**Why:** CSS custom properties were faster and safer than rewriting 800 LOC of structured styles in Tailwind utilities.
