/*
 * The APPLICATION registry — the one place both front-ends agree on what an
 * application IS and how to scope a request to it.
 *
 * Why this file exists at all, given that prefront-app and verdict deliberately
 * share no code (see prefront-ui/CLAUDE.md): everything else they duplicate is
 * PRESENTATION, and duplicated presentation drifts visibly — two pages look
 * different and someone notices. An application id or a Phoenix project name
 * drifting does not look like anything. It silently scopes a read to an
 * application that does not exist, and the page renders a confident, empty
 * result. That failure is invisible by construction, so this is the one thing
 * the two apps must not each keep their own copy of.
 *
 * It is NOT a workspace package on purpose. Adding one changes
 * pnpm-lock.yaml, and both Dockerfiles build with `pnpm install
 * --frozen-lockfile` — so a new package would break the image build until the
 * lockfile were regenerated. A Vite alias (`@apps`, wired in both
 * vite.config.ts and both tsconfig.json) shares the source with no dependency
 * graph change at all.
 *
 * SCOPE OF THIS FILE: identity and the keys used to scope a request. Nothing
 * about how an application is presented — prefront-app's Data Connector
 * prefills, role→agent-surface names and sample flows stay in its own
 * demos.ts, because only it has those surfaces.
 *
 * KEEPING IT HONEST: `phoenixProject` must match the demo compose's
 * PHOENIX_PROJECT_NAME, and `id` must match the `app_id` eval-engine resolves
 * (see application_isolation_design.md). They are the same string for every
 * bundled application; a deployment whose Phoenix projects are named
 * differently maps them server-side with EVAL_PROJECT_APP_MAP rather than
 * letting the two diverge here.
 */

export type AppId = "securebank" | "loanpro";

export interface AppIdentity {
  /** The canonical application id. Scopes /eval/* (`app`), /api/* (`demo`)
   *  and skill-builder (`app_id`). */
  id: AppId;
  label: string;
  /** One line on what this application IS — used wherever an app is named to
   *  the reader, so the two front-ends cannot describe it differently. */
  tagline: string;

  /** The Phoenix project this application's services report to: the ingestion
   *  partition, and what scopes every /oob/* read. Must equal the demo
   *  compose's PHOENIX_PROJECT_NAME. */
  phoenixProject: string;

  /** This application's scenario orchestrator, or "" when it has none.
   *  Absolute because it is a cross-origin fetch (those services send
   *  permissive CORS). "" means the application ships no runnable catalogue —
   *  Verdict must offer it as unrunnable rather than pointing at another
   *  application's orchestrator, which is how one app's results end up
   *  labelled as another's. */
  orchestratorUrl: string;

  /** Fields the runtime treats as sensitive: highlighted in a transcript when
   *  an ungoverned run surfaces them. Application vocabulary, never engine. */
  sensitiveFields: string[];
}

export const APPLICATIONS: AppIdentity[] = [
  {
    id: "loanpro",
    label: "LoanPro",
    tagline:
      "Loan origination — an ungoverned agent whose sessions exhibit every failure mode the out-of-band checks detect.",
    phoenixProject: "loanpro",
    orchestratorUrl: "http://localhost:8098",
    sensitiveFields: ["ssn", "tax_id", "bank_account_hint", "credit_score", "internal_risk_score"],
  },
  {
    id: "securebank",
    label: "SecureBank",
    tagline:
      "Retail banking — governed in-band by Prefront's MCP; no out-of-band tap and no scenario catalogue.",
    phoenixProject: "securebank",
    // Deliberately empty. SecureBank's orchestrator (:8095) runs a
    // governed-vs-ungoverned diff, NOT the session catalogue Verdict drives —
    // pointing Verdict at it would produce a confident, wrong-shaped result.
    orchestratorUrl: "",
    sensitiveFields: ["ssn"],
  },
];

/** LoanPro is the application both front-ends open on: it is the only one with
 *  a runnable catalogue and an out-of-band tap. */
export const DEFAULT_APP: AppId = "loanpro";

/** Resolve an id from a URL param or storage. Falls back to DEFAULT_APP rather
 *  than APPLICATIONS[0], so a stale value lands where the rest of the app
 *  defaults to instead of silently on a different application. */
export function getApp(id: string | null | undefined): AppIdentity {
  return (
    APPLICATIONS.find((a) => a.id === id) ??
    APPLICATIONS.find((a) => a.id === DEFAULT_APP) ??
    APPLICATIONS[0]
  );
}
