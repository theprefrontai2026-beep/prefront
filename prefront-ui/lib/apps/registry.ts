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

  /** This application's orchestrator, or "" when it has none at all. Absolute
   *  because it is a cross-origin fetch (those services send permissive CORS).
   *  Populated even when Verdict cannot DRIVE it — see `scenarioCatalogue`. */
  orchestratorUrl: string;

  /** Whether that orchestrator serves the SESSION CATALOGUE Verdict drives:
   *  `GET /api/scenarios` shaped `{families:[…]}` plus `GET /api/run`.
   *
   *  Separate from `orchestratorUrl` because "has no orchestrator" and "has one
   *  Verdict cannot drive" are different facts, and collapsing them into an
   *  empty URL hid the second: SecureBank's orchestrator is real and serves
   *  :8095, but its /api/scenarios returns a bare LIST and it exposes /api/diff
   *  instead of /api/run — a governed-vs-ungoverned diff, not a catalogue of
   *  sessions. Blanking the URL made the field look unconfigured when the
   *  address is in fact known and correct. */
  scenarioCatalogue: boolean;

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
    scenarioCatalogue: true,
    sensitiveFields: ["ssn", "tax_id", "bank_account_hint", "credit_score", "internal_risk_score"],
  },
  {
    id: "securebank",
    label: "SecureBank",
    tagline:
      "Retail banking — governed in-band by Prefront's MCP; no out-of-band tap and no scenario catalogue.",
    phoenixProject: "securebank",
    // Its real address — the field should show it rather than looking
    // unconfigured. But this orchestrator runs a governed-vs-ungoverned DIFF,
    // not the session catalogue Verdict drives: /api/scenarios returns a bare
    // list and there is no /api/run. So the URL is populated and the
    // capability is declared false.
    orchestratorUrl: "http://localhost:8095",
    scenarioCatalogue: false,
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
