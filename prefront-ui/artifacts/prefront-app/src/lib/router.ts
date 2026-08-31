/**
 * A ~70-line URL layer, deliberately not a router library.
 *
 * `App.tsx` keeps every tab MOUNTED and toggles them with `tab-hidden` CSS so
 * per-tab state survives navigation (children refetch off an `active` prop).
 * A `<Route>`-based library would unmount them and destroy that, so what this
 * app needs is the URL as a *synced mirror* of state it already holds — not a
 * rendering authority. That is `useLoc()` + `navigate()`, and nothing else.
 *
 * The snapshot is read at MODULE INIT, synchronously, so the route is known
 * before first paint — a deep link never flashes the Overview first.
 */
import { useSyncExternalStore } from "react";

// BASE_PATH is "/" at build (Dockerfile.ui), but read it rather than assume it.
const BASE = (import.meta.env.BASE_URL || "/").replace(/\/+$/, ""); // "" when "/"

export type Loc = {
  path: string;              // app-relative, base stripped, always leading "/"
  segs: string[];            // decoded, empty-stripped path segments
  query: URLSearchParams;
  key: string;               // path+search — the identity used by the no-op guard
};

function read(): Loc {
  const raw = window.location.pathname;
  const path = (BASE && raw.startsWith(BASE) ? raw.slice(BASE.length) : raw) || "/";
  const search = window.location.search;
  return {
    path,
    segs: path.split("/").filter(Boolean).map((s) => { try { return decodeURIComponent(s); } catch { return s; } }),
    query: new URLSearchParams(search),
    key: path + search,
  };
}

let snap: Loc = read();
const subs = new Set<() => void>();

function emit() {
  const next = read();
  if (next.key === snap.key) return;
  snap = next;
  subs.forEach((f) => f());
}

window.addEventListener("popstate", emit);

function subscribe(cb: () => void) { subs.add(cb); return () => { subs.delete(cb); }; }
const getSnap = () => snap;

/** Subscribe a component to the URL. Re-renders only when path+search change. */
export function useLoc(): Loc { return useSyncExternalStore(subscribe, getSnap, getSnap); }

/** Read the URL outside React (event handlers, module init). */
export function currentLoc(): Loc { return snap; }

/**
 * Navigate to an app-relative url ("/traces/findings?event=42").
 * Navigating to the URL we are already on is a NO-OP — that one guard is what
 * keeps a URL -> state -> URL cycle from looping.
 */
export function navigate(to: string, opts: { replace?: boolean } = {}) {
  const [p, s = ""] = to.split("?");
  const key = (p || "/") + (s ? `?${s}` : "");
  if (key === snap.key) return;
  const url = BASE + key;
  if (opts.replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
  emit();
}

/**
 * Build an href from a path plus a query patch. A null/undefined/"" value
 * DELETES the key, so callers can pass optional ids straight through.
 * `base` seeds the query (pass `loc.query` to keep the params already there).
 */
export function buildHref(
  path: string,
  patch: Record<string, string | number | null | undefined> = {},
  base?: URLSearchParams,
): string {
  const q = new URLSearchParams(base ? base.toString() : "");
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === undefined || v === "") q.delete(k);
    else q.set(k, String(v));
  }
  const s = q.toString();
  return (path || "/") + (s ? `?${s}` : "");
}

/** Patch query params on the CURRENT path. Defaults to replace (see the
 *  history policy: query-only changes are corrections, not destinations). */
export function setParams(
  patch: Record<string, string | number | null | undefined>,
  opts: { replace?: boolean } = {},
) {
  navigate(buildHref(snap.path, patch, snap.query), { replace: opts.replace !== false });
}

/** Absolute URL for the clipboard — a relative path in a paste buffer is useless. */
export function absUrl(rel: string): string {
  return `${window.location.origin}${BASE}${rel.startsWith("/") ? rel : "/" + rel}`;
}
