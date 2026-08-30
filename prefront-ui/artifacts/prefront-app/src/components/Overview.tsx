/*
 * Overview — the buyer-facing home screen, editorial layout.
 *
 * A single-glance shadow-evaluation report: what Prefront WOULD have decided
 * before each action ran, for the demo's (ungoverned) agent. Every number is
 * LIVE — never a fixture — from eval-engine (/eval/*) and oob-ingest (/oob/*)
 * via hooks/useOverviewData.ts. Nothing here claims a block that happened.
 *
 * The demo is ungoverned, so the framing is always "would have" — nothing on
 * this page was actually blocked.
 *
 * Domain-neutral: this file names no demo's tables, roles, or fields. Policy
 * section numbers come from finding data (source.section); sensitive-field
 * names, when used, come from demos.ts — never a literal here.
 */

import { useState, type ReactNode } from "react";
import type { DemoConfig } from "../demos";
import {
  useOverviewData, byEffect, byRule, familySpread, severityBreakdown,
  findingsPerDay, topRulesShare, type SeverityRow,
} from "../hooks/useOverviewData";
import { useSeverityRules } from "../hooks/useSeverityRules";
import { SEVERITY_META } from "../severity";
import { num, ago, parseSource, SessionFlyout } from "./Observability";

// Verdict (the scenario runner) lives on its own port on the same host.
const verdictUrl = () => `${window.location.protocol}//${window.location.hostname}:5180`;

// Effect → the badge shown on a finding. `block` carries the accent; the rest
// stay quiet. Labels are short, uppercase, and match the runtime vocabulary.
const EFFECT_BADGE: Record<string, { label: string; tone: string }> = {
  block:             { label: "Block",    tone: "block" },
  approval_required: { label: "Approval", tone: "approval" },
  flag:              { label: "Flag",     tone: "flag" },
};
const effectBadge = (e: string) => EFFECT_BADGE[e] ?? { label: e || "—", tone: "flag" };

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="pf-ov2-empty">
      {text} <a className="pf-ov2-link" href={verdictUrl()} target="_blank" rel="noreferrer">Run a scenario in Verdict →</a>
    </div>
  );
}

// The hero sparkline: findings per day over the fetched window. Pure SVG, no
// library. An empty window renders flat bars (real: nothing evaluated yet).
function Spark({ bars, peak }: { bars: { label: string; count: number; today: boolean }[]; peak: number }) {
  const max = Math.max(peak, 1);
  return (
    <div className="pf-ov2-spark">
      <div className="pf-ov2-spark-bars">
        {bars.map((b, i) => (
          <div key={i} className={`pf-ov2-spark-col ${b.today ? "today" : ""}`} title={`${b.label}: ${b.count}`}>
            <div className="pf-ov2-spark-fill" style={{ height: `${Math.max((b.count / max) * 100, 3)}%` }} />
          </div>
        ))}
      </div>
      <div className="pf-ov2-spark-axis">
        <span>{bars[0]?.label}</span>
        <span>today · {bars[bars.length - 1]?.count ?? 0}</span>
      </div>
    </div>
  );
}

/* ── page ───────────────────────────────────────────────────────────────── */

export default function Overview({ demo, active = true, onOpenFindings, onOpenFindingsSeverity, onOpenDecisions, onOpenObservability, onOpenSettings }: {
  demo: DemoConfig; active?: boolean;
  onOpenFindings: (effect?: string) => void; onOpenFindingsSeverity: (severity?: string) => void;
  onOpenDecisions: () => void; onOpenObservability: () => void; onOpenSettings: () => void;
}) {
  const d = useOverviewData(demo, active);
  const { rules: severityRules } = useSeverityRules(demo.id, active);
  const [flyout, setFlyout] = useState<{ sessionId: string; spanId: string | null; eventId: string | null; detail?: string; source?: string } | null>(null);

  const ev = d.evalStatus;
  const ch = ev?.clickhouse;
  const rp = ev?.worker?.rule_pack;
  const ic = ev?.worker?.intent_catalog;
  const k = d.overview?.kpis ?? {};

  const findings = d.findings;
  const total = findings.length;
  const effects = byEffect(findings);
  const ruleRows = byRule(findings, 3);
  const sev = severityBreakdown(findings, severityRules);
  const fam = familySpread(findings);
  const famTotal = fam.reduce((s, f) => s + f.count, 0) || 1;
  const spark = findingsPerDay(findings, 7);
  const topShare = topRulesShare(byRule(findings, 3), total, 3);
  const offCatalog = d.sessions.reduce((n, s) => n + (s.off_catalog_calls || 0), 0);
  // Evaluation scope, summed across the sessions the engine evaluated.
  const sessionsEvaluated = ch?.sessions_evaluated ?? 0;
  const turnsEvaluated = d.sessions.reduce((n, s) => n + (s.turns || 0), 0);
  const toolCallsEvaluated = d.sessions.reduce((n, s) => n + (s.tool_calls || 0), 0);
  const cov = d.coverage?.rule_pack;
  const latest = findings.slice(0, 5);

  // Sub-detail for the "would have blocked" card: the policy § most block
  // findings cite (from the data, never hardcoded). Empty when none carry one.
  const blockSection = (() => {
    const secs = new Map<string, number>();
    for (const f of findings) if (f.effect === "block") {
      const s = parseSource(f.source)?.section?.split(/[/,]/)[0]?.trim();
      if (s) secs.set(s, (secs.get(s) ?? 0) + 1);
    }
    return [...secs.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
  })();

  const noEval = d.errors.eval;
  const nothingYet = !noEval && sessionsEvaluated === 0;

  const KPIS: { label: string; value: string; sub: ReactNode; accent?: boolean; onClick?: () => void }[] = [
    { label: "Would have blocked", value: num(effects.block), accent: effects.block > 0,
      sub: blockSection ? <>Policy · §{blockSection}</> : "action not allowed", onClick: () => onOpenFindings("block") },
    { label: "Needs a human", value: num(effects.approval_required),
      sub: "approval required", onClick: () => onOpenFindings("approval_required") },
    { label: "Rules live", value: num(rp?.rule_count),
      sub: ic?.configured ? <>{num(ic.intent_count)} intents in catalog</> : "no catalog configured" },
    { label: "Off-catalog calls", value: num(offCatalog),
      sub: "tools no intent declares", onClick: onOpenObservability },
  ];

  return (
    <div className="pf-ov2">
      {/* ── Hero ── */}
      <header className="pf-ov2-hero">
        <div className="pf-ov2-hero-main">
          <div className="pf-ov2-eyebrow">
            Shadow evaluation{ev && <> · engine {ev.engine_version}</>}
          </div>
          <h1 className="pf-ov2-headline">
            {d.loading && !ev ? "…" : num(sessionsEvaluated)} sessions evaluated.{" "}
            <span className="pf-ov2-accent">{num(total)} would not have been allowed.</span>
          </h1>
          <p className="pf-ov2-lede">
            Prefront evaluated every action before execution. Nothing was stopped in production —
            each finding carries the conversation, the clause it breached, and the trace.
            {rp?.configured
              ? <> {num(rp.rule_count)} rules{ic?.configured && <> and {num(ic.intent_count)} intents</>} are live.</>
              : <> No rule pack is configured — Family&nbsp;1/3 checks are idle.</>}
          </p>
          {noEval && <div className="pf-ov2-error">eval-engine unreachable: {noEval}</div>}
          {nothingYet && <EmptyHint text={`No sessions evaluated yet — eval-engine polls every ${ev?.worker?.poll_seconds ?? "few"}s.`} />}
        </div>
        <div className="pf-ov2-hero-aside">
          <div className="pf-ov2-eyebrow">Findings / day{spark.peak > 0 && <span className="pf-ov2-peak">peak {spark.peak}</span>}</div>
          <Spark bars={spark.bars} peak={spark.peak} />
        </div>
      </header>

      {/* ── evaluation scope ── */}
      <div className="pf-ov2-scope">
        <span className="pf-ov2-eyebrow">Evaluated</span>
        <span className="pf-ov2-scope-item"><b>{num(sessionsEvaluated)}</b> sessions</span>
        <span className="pf-ov2-scope-item"><b>{num(turnsEvaluated)}</b> turns</span>
        <span className="pf-ov2-scope-item"><b>{num(toolCallsEvaluated)}</b> tool calls</span>
        {cov?.configured && (
          <span className="pf-ov2-scope-item" title={cov.never_fired > 0 ? `Never hit: ${cov.never_fired_ids.join(", ")}` : "Every rule in the pack has fired at least once"}>
            <b className={cov.never_fired > 0 ? "pf-ov2-accent" : ""}>{num(cov.never_fired)}</b> rules never hit
          </span>
        )}
      </div>

      {/* ── KPI row ── */}
      <div className="pf-ov2-kpis">
        {KPIS.map((kpi) => {
          const inner = (
            <>
              <div className="pf-ov2-kpi-label">{kpi.label}</div>
              <div className={`pf-ov2-kpi-value ${kpi.accent ? "accent" : ""}`}>{kpi.value}</div>
              <div className="pf-ov2-kpi-sub">{kpi.sub}</div>
            </>
          );
          return kpi.onClick
            ? <button key={kpi.label} type="button" className="pf-ov2-kpi clickable" onClick={kpi.onClick}>{inner}</button>
            : <div key={kpi.label} className="pf-ov2-kpi">{inner}</div>;
        })}
      </div>

      {/* ── Rule-evaluation band ── */}
      <div className="pf-ov2-band">
        <div className="pf-ov2-band-lead">
          <div className="pf-ov2-eyebrow">Rule evaluations</div>
          <div className="pf-ov2-band-value">{num(ch?.verdicts)}</div>
          <div className="pf-ov2-kpi-sub">
            {num(rp?.rule_count)} live rules{cov?.configured && <> · {cov.fired}/{cov.total} ever hit</>}
          </div>
        </div>
        <div className="pf-ov2-band-mid">
          <div className="pf-ov2-band-head">
            <span className="pf-ov2-eyebrow">Findings by family</span>
            <span className="pf-ov2-kpi-sub">{num(total)} total · top 3 checks carry {topShare}%</span>
          </div>
          <div className="pf-ov2-stack">
            {fam.map((f, i) => f.count > 0 && (
              <div key={f.key} className={`pf-ov2-stack-seg s${i}`} style={{ width: `${(f.count / famTotal) * 100}%` }} title={`${f.label}: ${f.count}`} />
            ))}
          </div>
          <div className="pf-ov2-legend">
            {fam.map((f, i) => (
              <span key={f.key} className="pf-ov2-legend-item">
                <span className={`pf-ov2-swatch s${i}`} />
                {f.label} · {num(f.count)}{f.rules > 0 && <span className="pf-ov2-kpi-sub"> · {f.rules} rules</span>}
              </span>
            ))}
          </div>
        </div>
        <div className="pf-ov2-band-tail">
          <div className="pf-ov2-eyebrow">Satisfied</div>
          <div className="pf-ov2-band-value quiet">{num(ch?.conformance_tags)}</div>
          <div className="pf-ov2-kpi-sub">rules applied, policy cited</div>
        </div>
      </div>

      {/* ── Two panels: severity insights · latest findings ── */}
      <div className="pf-ov2-cols">
        {/* Severity insights (replaces obligation coverage) */}
        <section className="pf-ov2-panel">
          <div className="pf-ov2-panel-head">
            <div>
              <h2>Severity breakdown</h2>
              <div className="pf-ov2-kpi-sub">{num(total)} findings · triage priority</div>
            </div>
            <button className="pf-ov2-link" type="button" onClick={() => onOpenFindings()}>Open the log →</button>
          </div>
          {total === 0 ? <EmptyHint text="No findings yet — either nothing violated, or no rule pack / intent catalog is configured." /> : (
            <table className="pf-ov2-sev">
              <thead>
                <tr><th>Severity</th><th>Share</th><th>Top driver</th><th className="r">Count</th></tr>
              </thead>
              <tbody>
                {sev.map((row: SeverityRow) => (
                  <tr key={row.level} className={row.count ? "clickable" : "muted"}
                      onClick={row.count ? () => onOpenFindingsSeverity(row.level) : undefined}>
                    <td><span className={`pf-ov2-badge sev-${SEVERITY_META[row.level].tone}`}>{SEVERITY_META[row.level].label}</span></td>
                    <td>
                      <div className="pf-ov2-sev-share">
                        <div className="pf-ov2-sev-track"><div className={`pf-ov2-sev-fill sev-${SEVERITY_META[row.level].tone}`} style={{ width: `${Math.max(row.share * 100, row.count ? 3 : 0)}%` }} /></div>
                        <span className="pf-ov2-sev-pct">{Math.round(row.share * 100)}%</span>
                      </div>
                    </td>
                    <td className="pf-ov2-driver" title={row.topDriver}>
                      {row.topDriver ? <><span className="mono">{row.topDriver}</span>{row.topSection && <span className="pf-ov2-kpi-sub"> · §{row.topSection}</span>}</> : <span className="pf-ov2-kpi-sub">—</span>}
                    </td>
                    <td className="r"><span className="pf-ov2-count">{num(row.count)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="pf-ov2-panel-foot">
            <span className="pf-ov2-kpi-sub">Severity is derived from each finding's family + effect.</span>
            <button className="pf-ov2-link" type="button" onClick={onOpenSettings}>Tune the mapping in Settings →</button>
          </div>
        </section>

        {/* Latest findings */}
        <section className="pf-ov2-panel">
          <div className="pf-ov2-panel-head">
            <h2>Latest findings</h2>
            <button className="pf-ov2-link" type="button" onClick={() => onOpenFindings()}>Open the log →</button>
          </div>
          {latest.length === 0 ? <EmptyHint text="No findings recorded yet." /> : (
            <div className="pf-ov2-feed">
              {latest.map((f) => {
                const b = effectBadge(f.effect);
                const src = parseSource(f.source);
                return (
                  <button key={f.event_id || f.session_id + f.check_id + f.evidence_excerpt} type="button" className="pf-ov2-feed-row"
                          onClick={() => setFlyout({ sessionId: f.session_id, spanId: f.evidence_span_ids?.[0] ?? null, eventId: f.event_id || null, detail: f.detail, source: f.source })}>
                    <div className="pf-ov2-feed-top">
                      <span className={`pf-ov2-badge sev-${b.tone}`}>{b.label}</span>
                      <span className="pf-ov2-feed-fam">{f.family_label || f.family}</span>
                      <span className="pf-ov2-feed-time">{ago(f.evaluated_at).replace(" ago", "")}{f.event_id && <> · evt {f.event_id}</>}</span>
                    </div>
                    <div className="pf-ov2-feed-detail" title={f.detail}>{f.detail}</div>
                    <div className="pf-ov2-feed-meta">
                      <span className="mono">{f.rule_id || f.check_id}</span>
                      {f.user_query && <span className="pf-ov2-feed-q" title={f.user_query}> · “{f.user_query}”</span>}
                      {src?.section && <span className="pf-ov2-kpi-sub"> · §{src.section}</span>}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
          <div className="pf-ov2-panel-foot">
            <span className="pf-ov2-kpi-sub">Evidence attached to every row — conversation, clause, trace.</span>
          </div>
        </section>
      </div>

      {/* ── Foot ── */}
      <div className="pf-ov2-foot">
        {d.oobStatus?.clickhouse?.spans != null && <>{num(d.oobStatus.clickhouse.spans)} spans ingested · </>}
        <button className="pf-ov2-link" type="button" onClick={onOpenObservability}>Observability →</button>
        {" · "}
        <button className="pf-ov2-link" type="button" onClick={onOpenDecisions}>Decision log →</button>
        {" · "}
        <button className="pf-ov2-link" type="button" onClick={d.reload} disabled={d.loading}>{d.loading ? "Refreshing…" : "Refresh ↻"}</button>
      </div>

      {flyout && (
        <SessionFlyout sessionId={flyout.sessionId} initialSpanId={flyout.spanId} eventId={flyout.eventId}
                       findingDetail={flyout.detail} findingSource={flyout.source} refreshKey={0}
                       onClose={() => setFlyout(null)} />
      )}
    </div>
  );
}
