/*
 * Overview — the buyer-facing home screen.
 *
 * Five dashboard sections, one per buyer concern (before-execution
 * decisions, rule execution, business-context controls, decision context,
 * evidence), each backed by LIVE evidence — never a fixture: eval-engine's
 * shadow evaluation of the demo's (ungoverned) agent: what Prefront WOULD
 * have decided before each action ran. Copy says so wherever a number could
 * be mistaken for inline enforcement; nothing here claims a block that never
 * happened.
 *
 * Data: hooks/useOverviewData.ts. Primitives: Kpi/Bars/Empty + formatters
 * from Observability.tsx; the flyout is the same SessionFlyout the Findings
 * table opens. Section titles/captions below are UI copy, not domain
 * vocabulary — nothing in this file names a demo's tables, roles, or fields.
 */

import { useState } from "react";
import type { DemoConfig } from "../demos";
import { useDecisionFeed, type PolicyStat } from "../hooks/useDecisionFeed";
import {
  useOverviewData, byEffect, byRule, byFamily, iamVsContext, sensitiveFindings,
  distinctIntents, offCatalogCalls, citedTags, type Effect,
} from "../hooks/useOverviewData";
import { Kpi, Bars, Empty, num, ms, ago, parseSource, SessionFlyout } from "./Observability";

/* ── section copy ───────────────────────────────────────────────────────── */
// Plain dashboard headings. Each section still maps to one buyer concern
// (before-execution decisions / rule execution / business-context controls /
// decision context / evidence) but reads as a dashboard, not a pitch.

const SECTIONS = {
  layer:    { title: "Decisions before execution", caption: "What the agent did, next to what Prefront would have allowed before it acted." },
  tools:    { title: "Rules applied",               caption: "Which rule fired, how often, and the policy section it comes from." },
  iam:      { title: "Business-context controls",  caption: "Findings that need business context to decide — beyond who-can-access-what." },
  catalog:  { title: "Decision context",           caption: "Rules and intents available to agents, and how much of that context sessions actually used." },
  evidence: { title: "Decision evidence",          caption: "Each decision leaves a trace: facts used, rule applied, approval, outcome, and the policy clause." },
} as const;

const SHADOW_NOTE = "Shadow evaluation of an ungoverned agent — nothing here was blocked. These are the decisions Prefront would have made before execution.";

const EFFECT_LABEL: Record<Effect, { label: string; sub: string; tone: string }> = {
  block:             { label: "Would have blocked",       sub: "action not allowed",       tone: "red" },
  approval_required: { label: "Would need approval",      sub: "routed to a human first",  tone: "amber" },
  flag:              { label: "Would have flagged",       sub: "allowed, evidence noted",  tone: "teal" },
};
const effectTone = (e: string) => EFFECT_LABEL[e as Effect]?.tone ?? "teal";

// Verdict (the scenario runner) lives on its own port on the same host.
const verdictUrl = () => `${window.location.protocol}//${window.location.hostname}:5180`;

/* ── shell ──────────────────────────────────────────────────────────────── */

function Section({ s, children, aside }: {
  s: { title: string; caption: string }; children: React.ReactNode; aside?: React.ReactNode;
}) {
  return (
    <section className="pf-panel pf-ov-section">
      <div className="pf-ov-head">
        <div>
          <h2>{s.title}</h2>
          <p className="pf-ov-answer">{s.caption}</p>
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="pf-dash-feed-status">
      {text} <a className="pf-dash-link" href={verdictUrl()} target="_blank" rel="noreferrer">Run a scenario in Verdict →</a>
    </div>
  );
}

/* ── governed runtime (conditional) ─────────────────────────────────────── */

const policyTone = (effect: PolicyStat["effect"]) =>
  effect === "block" ? "red" : effect === "mask" ? "teal" : effect === "approval" ? "amber" : "green";

// Only rendered when the decision store has governed traffic for this demo
// (a governed orchestrator ran). Never shows a zero-panel that would imply
// inline enforcement happened when it didn't.
function GovernedRuntime({ demo, onViewAll }: { demo: DemoConfig; onViewAll: () => void }) {
  const feed = useDecisionFeed(demo);
  const s = feed.stats;
  if (!s || !s.total) return null;
  const pctOf = (n: number) => Math.round((n / s.total) * 100);
  const max = feed.policies[0]?.count || 1;
  return (
    <section className="pf-panel pf-ov-section">
      <div className="pf-ov-head">
        <div>
          <h2>Inline enforcement</h2>
          <p className="pf-ov-answer">Decisions Prefront's runtime actually made on the request path for this deployment.</p>
        </div>
        <button className="pf-dash-link" type="button" onClick={onViewAll}>View decision log →</button>
      </div>
      <div className="pf-ov-contrast">
        <div>
          <div className="pf-dash-bars">
            {([["Allowed", s.allowed, "green"], ["Masked", s.masked, "teal"], ["Blocked", s.blocked, "red"], ["Approval required", s.approval, "amber"]] as const).map(([label, n, tone]) => (
              <div key={label} className="pf-dash-bar-row">
                <div className="pf-dash-bar-label">{label}</div>
                <div className="pf-dash-bar-track"><div className={`pf-dash-bar-fill ${tone}`} style={{ width: `${pctOf(n)}%` }} /></div>
                <div className="pf-dash-bar-pct">{pctOf(n)}%</div>
              </div>
            ))}
          </div>
        </div>
        <div className="pf-dash-pol">
          {feed.policies.slice(0, 5).map((p, i) => (
            <div key={p.policy} className={`pf-dash-pol-row${i === 0 ? " top" : ""}`}>
              <div className="pf-dash-pol-head">
                <span className="pf-dash-pol-name">{p.policy}</span>
                {p.effect && <span className={`pf-dash-chip ${policyTone(p.effect)}`}>{p.effect}</span>}
                <span className="pf-dash-pol-count">{num(p.count)}</span>
              </div>
              <div className="pf-dash-pol-track"><div className={`pf-dash-pol-fill ${policyTone(p.effect)}`} style={{ width: `${Math.max((p.count / max) * 100, 4)}%` }} /></div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ── page ───────────────────────────────────────────────────────────────── */

export default function Overview({ demo, active = true, onOpenFindings, onOpenDecisions, onOpenObservability }: {
  demo: DemoConfig; active?: boolean;
  onOpenFindings: (effect?: string) => void; onOpenDecisions: () => void; onOpenObservability: () => void;
}) {
  const d = useOverviewData(demo, active);
  const [flyout, setFlyout] = useState<{ sessionId: string; spanId: string | null; eventId: string | null; detail?: string; source?: string } | null>(null);

  const ev = d.evalStatus;
  const ch = ev?.clickhouse;
  const rp = ev?.worker?.rule_pack;
  const ic = ev?.worker?.intent_catalog;
  const evaluated = ch?.sessions_evaluated ?? 0;
  const effects = byEffect(d.findings);
  const rules = byRule(d.findings);
  const families = byFamily(d.findings);
  const iam = iamVsContext(d.findings);
  const sensitive = sensitiveFindings(d.findings, demo.sensitiveFields);
  const offCatalog = offCatalogCalls(d.sessions);
  const intentsUsed = distinctIntents(d.sessions);
  const cited = citedTags(d.conformance);
  const latest = d.findings.slice(0, 5);
  const k = d.overview?.kpis ?? {};
  const noEval = d.errors.eval;
  const nothingYet = !noEval && evaluated === 0;
  const windowNote = `last ${num(Math.min(d.findings.length, 500))} findings`;

  return (
    <div className="pf-dash">
      {/* ── Hero: decision evidence ── */}
      <section className="pf-panel pf-dash-hero">
        <div className="pf-dash-hero-score">
          <div className="pf-dash-hero-label">Decision evidence</div>
          <div className="pf-dash-hero-value" title="sessions eval-engine has evaluated">{d.loading && !ev ? "…" : num(evaluated)}</div>
          <div className="pf-dash-hero-sub">
            <span className="pf-dash-hero-grade">sessions evaluated</span>
            {ev && <span className="pf-dash-subtle">{ev.mode} evaluation · engine {ev.engine_version}</span>}
          </div>
          <div className="pf-dash-subtle" style={{ marginTop: 10 }}>
            {rp?.configured
              ? <>rule pack <span className="mono">{rp.source_skill}@{rp.source_skill_version}</span>{ic?.configured && <> · catalog v{ic.version}</>}</>
              : "no rule pack configured — Family 1/3 checks idle"}
          </div>
          {noEval && <div className="pf-oob-error" style={{ marginTop: 8 }}>eval-engine unreachable: {noEval}</div>}
          {nothingYet && <EmptyHint text={`No sessions evaluated yet — eval-engine polls every ${ev?.worker?.poll_seconds ?? "few"}s.`} />}
        </div>
        <div className="pf-dash-hero-counters">
          <button className="pf-dash-counter pf-ov-clickable" type="button" onClick={() => onOpenFindings()}>
            <div className={`pf-dash-counter-value ${(ch?.findings ?? 0) > 0 ? "red" : ""}`}>{num(ch?.findings)}</div>
            <div className="pf-dash-counter-label">Findings — decisions that would not have been allowed as-is</div>
          </button>
          <div className="pf-dash-counter">
            <div className="pf-dash-counter-value">{num(ch?.conformance_tags)}</div>
            <div className="pf-dash-counter-label">Rules applied and satisfied — positive evidence, policy cited</div>
          </div>
          <div className="pf-dash-counter">
            <div className="pf-dash-counter-value">{num(rp?.rule_count)}<span className="pf-ov-counter-sep">·</span>{num(ic?.intent_count)}</div>
            <div className="pf-dash-counter-label">Policy coverage — rules in the pack · intents in the catalog</div>
          </div>
        </div>
      </section>

      {/* ── 1. Head of AI ── */}
      <Section s={SECTIONS.layer} aside={<span className="pf-ov-note">{SHADOW_NOTE}</span>}>
        <div className="pf-ov-contrast">
          <div>
            <div className="pf-ov-contrast-head">What happened <span className="pf-dash-subtle">observability · last 24h</span></div>
            <div className="pf-oob-kpis">
              <Kpi label="traces" value={num(k.traces)} />
              <Kpi label="tool calls" value={num(k.tool_calls)} />
              <Kpi label="p95 latency" value={ms(k.p95_ms)} />
            </div>
          </div>
          <div>
            <div className="pf-ov-contrast-head">What should have been allowed <span className="pf-dash-subtle">decision · {windowNote}</span></div>
            <div className="pf-oob-kpis">
              {(Object.keys(EFFECT_LABEL) as Effect[]).map((e) => (
                <button key={e} type="button" className="pf-ov-clickable" onClick={() => onOpenFindings(e)} title={`Open findings filtered to effect=${e}`}>
                  <Kpi label={EFFECT_LABEL[e].label} value={num(effects[e])} sub={EFFECT_LABEL[e].sub} tone={EFFECT_LABEL[e].tone} />
                </button>
              ))}
            </div>
          </div>
        </div>
        {d.findings.length === 0 && !d.loading && <EmptyHint text="No findings yet — either nothing violated, or no rule pack / intent catalog is configured." />}
      </Section>

      {/* ── 2. CIO ── */}
      <Section s={SECTIONS.tools} aside={<Kpi label="verdicts recorded" value={num(ch?.verdicts)} sub="every check, incl. satisfied" />}>
        <div className="pf-ov-contrast-head">Which rule applied <span className="pf-dash-subtle">{windowNote}</span></div>
        {rules.length === 0
          ? <Empty text="No rule has fired yet." />
          : <Bars rows={rules} label={(r) => r.section ? `${r.key}  §${r.section}` : r.key} value={(r) => r.count} tone={(r) => effectTone(r.effect)} />}
        <div className="pf-ov-family-row">
          {[["family1", "policy rules"], ["family2", "integrity invariants"], ["family3", "intent conformance"]].map(([f, label]) => (
            <span key={f} className="pf-dash-subtle"><strong>{num(families[f])}</strong> {label}</span>
          ))}
        </div>
      </Section>

      {/* ── 3. CISO ── */}
      <Section s={SECTIONS.iam}>
        <div className="pf-oob-kpis">
          <Kpi label="role-only findings" value={num(iam.roleOnly)} sub="IAM could catch these" />
          <Kpi label="business-context findings" value={num(iam.context)} sub="IAM has no concept of these" tone={iam.context ? "red" : undefined} />
          <Kpi label="sensitive-field findings" value={num(sensitive)} sub={`${demo.sensitiveFields.length} fields watched`} tone={sensitive ? "amber" : undefined} />
          <Kpi label="off-catalog tool calls" value={num(offCatalog)} sub="actions no approved intent covers" tone={offCatalog ? "amber" : undefined} />
        </div>
      </Section>

      {/* ── 4. CDO ── */}
      <Section s={SECTIONS.catalog}>
        <div className="pf-oob-kpis">
          <Kpi label="rules in the pack" value={num(rp?.rule_count)} sub={rp?.configured ? `${rp.source_skill}@${rp.source_skill_version}` : "not configured"} />
          <Kpi label="intents in the catalog" value={num(ic?.intent_count)} sub={ic?.configured ? `catalog v${ic.version}` : "not configured"} />
          <Kpi label="intents actually invoked" value={num(intentsUsed)} sub="distinct, last 24h" />
          <Kpi label="catalog-conformance findings" value={num(families.family3)} sub={windowNote} tone={families.family3 ? "amber" : undefined} />
        </div>
      </Section>

      {/* ── 5. Compliance ── */}
      <Section s={SECTIONS.evidence} aside={<button className="pf-dash-link" type="button" onClick={() => onOpenFindings()}>All findings →</button>}>
        <div className="pf-ov-contrast">
          <div>
            <div className="pf-ov-contrast-head">Latest decision evidence <span className="pf-dash-subtle">click a row for the full trace</span></div>
            {latest.length === 0 ? <EmptyHint text="No evidence recorded yet." /> : (
              <div className="pf-dash-feed">
                {latest.map((f) => {
                  const src = parseSource(f.source);
                  return (
                    <button key={f.event_id || f.session_id + f.check_id + f.evidence_excerpt} type="button" className="pf-dash-feed-row pf-ov-clickable"
                            onClick={() => setFlyout({ sessionId: f.session_id, spanId: f.evidence_span_ids?.[0] ?? null, eventId: f.event_id || null, detail: f.detail, source: f.source })}>
                      <div className="pf-dash-feed-time">{ago(f.evaluated_at).replace(" ago", "")}</div>
                      <div className="pf-dash-feed-body">
                        <div className="pf-dash-feed-top">
                          <span className={`pf-dash-chip ${effectTone(f.effect)}`}>{f.effect}</span>
                          <span className="pf-dash-feed-intent mono">{f.rule_id || f.check_id}</span>
                          {f.event_id && <span className="pf-dash-subtle mono">#{f.event_id}</span>}
                        </div>
                        <div className="pf-dash-feed-meta pf-tr-truncate" title={f.detail}>{f.detail}</div>
                        {f.user_query && <div className="pf-dash-feed-meta pf-tr-truncate" title={f.user_query}>“{f.user_query}”</div>}
                        {src?.section && <div className="pf-find-cite pf-dash-subtle">{src.document} · §{src.section}</div>}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <div>
            <div className="pf-ov-contrast-head">Rules applied &amp; satisfied <span className="pf-dash-subtle">{num(ch?.conformance_tags)} total · newest cited</span></div>
            {cited.length === 0 ? <Empty text="No policy-cited conformance yet." /> : (
              <div className="pf-dash-feed">
                {cited.map((t, i) => (
                  <button key={t.session_id + t.check_id + t.rule_id + i} type="button" className="pf-dash-feed-row pf-ov-clickable"
                          onClick={() => setFlyout({ sessionId: t.session_id, spanId: t.evidence_span_ids?.[0] ?? null, eventId: null })}>
                    <div className="pf-dash-feed-time">{ago(t.evaluated_at).replace(" ago", "")}</div>
                    <div className="pf-dash-feed-body">
                      <div className="pf-dash-feed-top">
                        <span className="pf-dash-chip green">satisfied</span>
                        <span className="pf-dash-feed-intent mono">{t.rule_id || t.check_id}</span>
                        {t.section && <span className="pf-dash-subtle">§{t.section}</span>}
                      </div>
                      {t.clause_text && <blockquote className="pf-find-quote pf-ov-quote">“{t.clause_text.trim().slice(0, 140)}{t.clause_text.length > 140 ? "…" : ""}”<cite>{t.policy_document}</cite></blockquote>}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </Section>

      <GovernedRuntime demo={demo} onViewAll={onOpenDecisions} />

      <div className="pf-dash-subtle pf-ov-foot">
        {d.oobStatus?.clickhouse?.spans != null && <>{num(d.oobStatus.clickhouse.spans)} spans ingested · </>}
        <button className="pf-dash-link" type="button" onClick={onOpenObservability}>Observability →</button>
        {" · "}
        <button className="pf-dash-link" type="button" onClick={d.reload} disabled={d.loading}>{d.loading ? "Refreshing…" : "Refresh ↻"}</button>
      </div>

      {flyout && (
        <SessionFlyout sessionId={flyout.sessionId} initialSpanId={flyout.spanId} eventId={flyout.eventId}
                       findingDetail={flyout.detail} findingSource={flyout.source} refreshKey={0}
                       onClose={() => setFlyout(null)} />
      )}
    </div>
  );
}
