/*
 * Verdict's view of an application.
 *
 * This used to be a hardcoded single constant — `LOANPRO`, with the label,
 * orchestrator URL and sensitive fields copied by hand out of prefront-app's
 * demos.ts. That made Verdict a LoanPro-only app in three separate ways: it
 * could not be pointed at another application, it named LoanPro in its own
 * chrome, and its copied values could drift from the ones the rest of the
 * stack scopes by without anything noticing.
 *
 * The identity now comes from the shared registry (lib/apps/registry.ts, via
 * the `@apps` alias), so Verdict and prefront-app cannot disagree about what
 * an application is called, which Phoenix project holds its traces, or which
 * orchestrator runs its catalogue.
 */

export { APPLICATIONS, DEFAULT_APP, getApp } from "@apps";
export type { AppId, AppIdentity } from "@apps";

/** The selected application, from `?app=` — the URL is the source of truth so
 *  a Verdict link carries its application with it, the way every deep link in
 *  the main app already does. Falls back to the default for an absent or
 *  unrecognised value rather than throwing or picking arbitrarily. */
export function appFromUrl(search: string = window.location.search): string {
  return new URLSearchParams(search).get("app") || "";
}

/** Switch application by rewriting the URL and reloading.
 *
 * A reload rather than React state on purpose: Verdict holds a run's results,
 * an open session, and a fetched catalogue, all belonging to the application
 * that produced them. Carrying any of it across a switch is precisely the
 * cross-application leak this work exists to stop — and a half-cleared view is
 * harder to reason about than a fresh one. Verdict has no unsaved state to
 * lose, so the blunt instrument is the correct one here.
 */
export function selectApp(id: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("app", id);
  window.location.assign(url.toString());
}
