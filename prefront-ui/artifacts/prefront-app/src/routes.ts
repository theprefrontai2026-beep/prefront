/**
 * The URL grammar — one source of truth for both navigation and CopyLink.
 *
 *   path  = WHAT you are looking at (page, sub-view, the artifact you opened)
 *   query = the identifiers that qualify it (?event, ?span, ?rule) + ?demo
 *
 * Filters, searches, time ranges and pagination deliberately stay in memory:
 * a shared link is meant to reproduce a page and an artifact, not a transient
 * view of a table.
 */
import { buildHref, currentLoc, navigate } from "./lib/router";

/** Tab id (App.tsx's TABS, plus `settings` and the hidden `semantic`) -> path. */
export const TAB_PATH: Record<string, string> = {
  dashboard:  "/",
  data:       "/data",
  policy:     "/policy",
  bizgraph:   "/business-graph",
  graph:      "/data-graph",
  traces:     "/traces",
  flows:      "/flows",
  // `/oob` would be swallowed by the Vite dev proxy (it matches BARE prefixes
  // with startsWith, unlike nginx's trailing-slash locations), so the
  // Observability tab — whose id is still `oob` — is routed as /observability.
  oob:        "/observability",
  settings:   "/settings",
  semantic:   "/semantic",
};

const PATH_TAB: Record<string, string> = Object.fromEntries(
  Object.entries(TAB_PATH).map(([tab, p]) => [p.replace(/^\//, ""), tab]),
);

/** First path segment -> tab id. Anything unknown lands on the Overview,
 *  which is also what keeps PAGE_META[tab] from being undefined. */
export function tabFromPath(segs: string[]): string {
  return PATH_TAB[segs[0] ?? ""] ?? "dashboard";
}

/**
 * Is this location on the given tab? Every tab body stays MOUNTED (App.tsx
 * hides them with CSS), so a component reading `segs[1]` unguarded would read
 * another page's artifact id — e.g. the Data Graph seeing "sessions" while you
 * are on /observability/sessions/<id>, and "correcting" the URL out from under
 * it. Every path-derived value below is gated on this.
 */
export function onTab(segs: string[], tabId: string): boolean {
  return (segs[0] ?? "") === TAB_PATH[tabId].replace(/^\//, "");
}

/* ── href builders (shared by nav and CopyLink) ─────────────────────────── */

const enc = encodeURIComponent;

export const findingsHref = () => "/traces/findings";

export function findingHref(sessionId: string, eventId?: string | null, spanId?: string | null) {
  return buildHref(`/traces/findings/${enc(sessionId)}`, { event: eventId, span: spanId });
}

export const obsViewHref  = (view: string) => `/observability/${view}`;
export const sessionHref  = (id: string) => `/observability/sessions/${enc(id)}`;
export const traceHref    = (id: string, span?: string | null) =>
  buildHref(`/observability/traces/${enc(id)}`, { span });

export const graphNodeHref = (id: string) => `/data-graph/${enc(id)}`;
export const bizNodeHref   = (id: string) => `/business-graph/${enc(id)}`;

// "rules" is Policy Studio's default sub-view, so it is omitted from the URL.
export const policyDocHref = (docId: string, tab?: string) =>
  buildHref(`/policy/${enc(docId)}`, { tab: tab && tab !== "rules" ? tab : null });
export const ruleHref = (docId: string | null, ruleKey: string) =>
  buildHref(docId ? `/policy/${enc(docId)}` : "/policy", { rule: ruleKey });

/**
 * Navigate, carrying ?demo= along. Every navigation in the app goes through
 * this rather than `navigate` directly: the demo is global context, so losing
 * it on a tab switch would make the address bar un-shareable until the
 * DemoProvider put it back.
 */
export function navTo(href: string, opts: { replace?: boolean } = {}) {
  const demo = currentLoc().query.get("demo");
  navigate(demo ? withDemo(href, demo) : href, opts);
}

/** Add ?demo= to any app-relative href, preserving its existing params. */
export function withDemo(href: string, demoId: string): string {
  const [p, s = ""] = href.split("?");
  return buildHref(p, { demo: demoId }, new URLSearchParams(s));
}

/* ── guard: a route must never shadow a proxied backend prefix ──────────── */
// nginx proxies these with a trailing slash; Vite's dev proxy matches the bare
// prefix with startsWith. The dev rule is the stricter one, so assert on it —
// a route named /oob or /apidocs would work in prod and 502 in dev otherwise.
const RESERVED = ["/api", "/design", "/oob", "/eval", "/pii", "/assets"];
if (import.meta.env.DEV) {
  for (const p of Object.values(TAB_PATH)) {
    if (RESERVED.some((r) => p.startsWith(r))) {
      throw new Error(`Route ${p} collides with a proxied backend prefix (${RESERVED.join(" ")})`);
    }
  }
}
