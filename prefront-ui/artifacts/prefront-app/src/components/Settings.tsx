/*
 * Settings — the "under the hood" configuration surface, reached from the gear
 * in the sidebar (not part of the numbered pipeline). Two sections, persisted
 * in two different places, for a reason worth stating:
 *
 *   Checks (`ChecksSection`) — which of the engine's checks this deployment
 *     runs, grouped by family. Talks to EVAL-ENGINE (GET/PUT/DELETE
 *     /eval/checks), so it is DEPLOYMENT-wide, not per demo: `/eval/*` is the
 *     engine's own surface and has no notion of a demo (the same caveat
 *     prefront-ui/CLAUDE.md records for `/oob/*`). Disabling a check stops it
 *     evaluating AND hides its existing verdicts everywhere, so it is a real
 *     switch rather than a display filter — the panel copy says so.
 *   Finding severity — a customer-editable ordered rule-list that derives each
 *     finding's severity from its family + effect (first-match-wins). Talks to
 *     API-SERVER (GET/PUT/DELETE /api/settings/severity) and IS per demo,
 *     because severity is a display-layer rating the engine never sees.
 *
 * Domain-neutral by construction — both key on engine concepts only (family,
 * effect, check id), never on a deployment's tables, roles or fields.
 */

import { useCallback, useEffect, useState } from "react";
import type { DemoConfig } from "../demos";
import {
  getSeverityRules, saveSeverityRules, resetSeverityRules,
  getChecks, saveChecks, resetChecks,
  type CheckInfo, type CheckFamily, type ChecksResponse,
} from "../api";
import {
  DEFAULT_SEVERITY_RULES, SEVERITY_META, SEVERITY_ORDER,
  FAMILY_OPTIONS, EFFECT_OPTIONS,
  type SeverityLevel, type SeverityRule,
} from "../severity";
import { CHECK_HELP } from "../checkHelp";

function Pick({ value, options, onChange }: {
  value: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
}) {
  return (
    <select className="pf-set-pick" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

/* ── Checks: enable/disable each check, per family ────────────────────────── */

/** The worked example for one check: what has to be configured for it to run
 *  at all, what makes it fire, and one concrete case with the line the engine
 *  writes for it. Content lives in ../checkHelp.ts (see its header for why the
 *  examples are hypothetical rather than read from the deployment). A check the
 *  engine adds before the help does still renders — with a note, never a gap. */
// `backticked` spans in the help text become real <code>, so a config key or a
// tool name reads as one instead of as literal backticks. Deliberately just
// this one rule — the help is prose with identifiers in it, not markdown.
function withCode(text: string) {
  return text.split(/`([^`]+)`/).map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part));
}

function CheckExplainer({ checkId }: { checkId: string }) {
  const help = CHECK_HELP[checkId];
  if (!help) {
    return (
      <div className="pf-chk-help">
        <p className="pf-chk-help-line">
          No worked example for <code>{checkId}</code> yet — the engine describes it above.
        </p>
      </div>
    );
  }
  return (
    <div className="pf-chk-help">
      <p className="pf-chk-help-line"><span className="pf-chk-help-k">Needs</span>{withCode(help.needs)}</p>
      <p className="pf-chk-help-line"><span className="pf-chk-help-k">Flags when</span>{withCode(help.flags)}</p>
      <p className="pf-chk-help-line"><span className="pf-chk-help-k">Example</span>{withCode(help.given)}</p>
      {/* The verdict this example produces, in the engine's own words - the
         same sentence the reader will meet in Findings. */}
      <p className="pf-chk-help-emits">→ {help.emits}</p>
    </div>
  );
}

/** One check row: a switch, its name and id, what it asserts, and a toggle for
 *  the worked example. The explainer button sits OUTSIDE the <label> — inside
 *  it, every click would also flip the switch. */
function CheckRow({ c, disabled, open, onOpen, onToggle }: {
  c: CheckInfo; disabled: boolean; open: boolean; onOpen: (open: boolean) => void; onToggle: (on: boolean) => void;
}) {
  return (
    <div className={`pf-chk-item ${open ? "open" : ""}`}>
      <div className="pf-chk-item-row">
        <label className={`pf-chk-row ${c.enabled ? "" : "off"}`}>
          <input type="checkbox" role="switch" className="pf-chk-switch"
                 checked={c.enabled} disabled={disabled}
                 onChange={(e) => onToggle(e.target.checked)} />
          <span className="pf-chk-body">
            <span className="pf-chk-head">
              <span className="pf-chk-title">{c.title}</span>
              <code className="pf-chk-id">{c.check_id}</code>
              {c.population && (
                <span className="pf-dash-chip teal" title="Computed on demand across many sessions (POST /eval/population), not during a session's own evaluation">
                  on demand
                </span>
              )}
            </span>
            <span className="pf-chk-detail">{c.detail}</span>
          </span>
        </label>
        <button type="button" className="pf-chk-help-btn" aria-expanded={open}
                aria-label={`${open ? "Hide" : "Show"} the example for ${c.title}`}
                title="What configures this check, and a worked example"
                onClick={() => onOpen(!open)}>
          {open ? "✕" : "?"}
        </button>
      </div>
      {open && <CheckExplainer checkId={c.check_id} />}
    </div>
  );
}

function ChecksSection() {
  const [data, setData] = useState<ChecksResponse | null>(null);
  const [disabledIds, setDisabledIds] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  // Which checks have their worked example open. `explainAll` opens every one
  // at once (read the whole reference top to bottom); the per-check set is
  // what the individual "?" buttons edit.
  const [explained, setExplained] = useState<Set<string>>(new Set());
  const [explainAll, setExplainAll] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const apply = useCallback((d: ChecksResponse) => {
    setData(d);
    setDisabledIds(new Set(d.disabled));
    setStatus("ready");
  }, []);

  const load = useCallback(() => {
    setStatus("loading"); setError("");
    getChecks().then(apply).catch((e) => { setError(String(e?.message || e)); setStatus("error"); });
  }, [apply]);

  useEffect(() => { load(); }, [load]);

  // The switches render from `disabledIds`, not from the `enabled` flag the
  // engine sent, so edits stay local until Save — same shape as the severity
  // editor below. Saving makes the engine re-evaluate, which is not something
  // to fire on every click.
  const isOn = (id: string) => !disabledIds.has(id);
  const toggle = (id: string, on: boolean) => {
    setMsg("");
    setDisabledIds((s) => {
      const next = new Set(s);
      if (on) next.delete(id); else next.add(id);
      return next;
    });
  };
  const setFamily = (fam: CheckFamily, on: boolean) => {
    setMsg("");
    setDisabledIds((s) => {
      const next = new Set(s);
      for (const c of fam.checks) { if (on) next.delete(c.check_id); else next.add(c.check_id); }
      return next;
    });
  };

  const stored = new Set(data?.disabled ?? []);
  const dirty = stored.size !== disabledIds.size || [...disabledIds].some((id) => !stored.has(id));
  const total = data?.total ?? 0;
  const enabledCount = total - disabledIds.size;

  const save = async () => {
    setSaving(true); setMsg(""); setError("");
    try {
      const d = await saveChecks([...disabledIds].sort());
      apply(d);
      setMsg(d.unknown?.length
        ? `Saved. Ignored ${d.unknown.length} unknown id(s).`
        : "Saved — the engine is re-evaluating.");
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setSaving(false); }
  };
  const reset = async () => {
    setSaving(true); setMsg(""); setError("");
    try { apply(await resetChecks()); setMsg("All checks enabled."); }
    catch (e: any) { setError(String(e?.message || e)); }
    finally { setSaving(false); }
  };

  return (
    <section className="pf-panel">
      <div className="pf-dash-panel-head">
        <h2>Checks</h2>
        <span className="pf-dash-subtle">
          {status === "ready" ? `${enabledCount} of ${total} enabled · deployment-wide` : "…"}
        </span>
        <div className="pf-dash-panel-actions">
          <button className="pf-dash-link" type="button"
                  onClick={() => { setExplainAll((v) => !v); setExplained(new Set()); }}
                  title="What configures each check, what makes it fire, and a worked example with the line the engine writes">
            {explainAll ? "Hide examples" : "Explain every check"}
          </button>
        </div>
      </div>
      <p className="pf-hint" style={{ marginTop: 0 }}>
        Which of the engine’s checks this deployment runs. A disabled check
        stops producing verdicts <em>and</em> its existing ones stop being
        counted anywhere — Findings, the Overview, Compliance — so this is a
        real switch, not a display filter. Nothing is deleted: re-enabling
        brings the history straight back, and the engine re-evaluates the
        sessions it can still see so the gap fills in too. One setting for the
        whole deployment, not per demo — the evaluation engine has no notion
        of one.
      </p>

      {status === "error" && <div className="pf-dash-feed-status error">Couldn’t load checks ({error}).</div>}

      {data && (
        <div className="pf-chk-families">
          {data.families.map((fam) => {
            const on = fam.checks.filter((c) => isOn(c.check_id)).length;
            return (
              <div key={fam.family} className="pf-chk-family">
                <div className="pf-chk-family-head">
                  <h3>{fam.label}</h3>
                  <span className="pf-dash-subtle">{on} of {fam.checks.length}</span>
                  {!fam.configured && (
                    <span className="pf-dash-chip amber"
                          title="This family reads a published artifact that isn’t configured — a rule pack for Policy, an intent catalog for Conformance. Its checks are idle whatever these switches say.">
                      not configured
                    </span>
                  )}
                  <div style={{ flex: 1 }} />
                  <button className="pf-dash-link" type="button" disabled={saving || on === fam.checks.length}
                          onClick={() => setFamily(fam, true)}>Enable all</button>
                  <button className="pf-dash-link" type="button" disabled={saving || on === 0}
                          onClick={() => setFamily(fam, false)}>Disable all</button>
                </div>
                <div className="pf-chk-list">
                  {fam.checks.map((c) => (
                    <CheckRow key={c.check_id} c={{ ...c, enabled: isOn(c.check_id) }}
                              disabled={saving}
                              open={explainAll || explained.has(c.check_id)}
                              onOpen={(open) => setExplained((prev) => {
                                const next = new Set(prev);
                                if (open) next.add(c.check_id); else next.delete(c.check_id);
                                return next;
                              })}
                              onToggle={(v) => toggle(c.check_id, v)} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="pf-set-actions">
        {data && <span className="pf-dash-subtle">Evaluation version <code className="pf-chk-id">checks@{data.version}</code></span>}
        <div style={{ flex: 1 }} />
        {msg && <span className="pf-dash-subtle">{msg}</span>}
        {error && status !== "error" && <span className="pf-dash-feed-status error" style={{ padding: 0 }}>{error}</span>}
        {/* Distinct from the per-family "Enable all" above: this one clears the
            STORED set, so the deployment goes back to the engine's default of
            every check on. Named like the severity panel's own reset. */}
        <button className="pf-dash-link" type="button" onClick={reset}
                disabled={saving || !data || data.disabled.length === 0}>Reset to defaults</button>
        <button className="pf-btn primary sm" type="button" onClick={save} disabled={saving || !dirty}>
          {saving ? "Saving…" : dirty ? "Save" : "Saved"}
        </button>
      </div>
    </section>
  );
}

export default function Settings({ demo, active = true, onSaved }: {
  demo: DemoConfig; active?: boolean; onSaved?: () => void;
}) {
  const [rules, setRules] = useState<SeverityRule[]>(DEFAULT_SEVERITY_RULES);
  const [isDefault, setIsDefault] = useState(true);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setStatus("loading"); setError("");
    getSeverityRules(demo.id)
      .then((d) => { setRules(d.rules?.length ? d.rules : DEFAULT_SEVERITY_RULES); setIsDefault(!!d.isDefault); setStatus("ready"); })
      .catch((e) => { setError(String(e?.message || e)); setStatus("error"); });
  }, [demo.id]);

  useEffect(() => { if (active) load(); }, [active, load]);

  const update = (i: number, patch: Partial<SeverityRule>) =>
    setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const move = (i: number, dir: -1 | 1) =>
    setRules((rs) => {
      const j = i + dir;
      if (j < 0 || j >= rs.length) return rs;
      const next = rs.slice();
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  const remove = (i: number) => setRules((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : rs));
  const add = () => setRules((rs) => [...rs, { family: null, effect: null, severity: "low" }]);

  const save = async () => {
    setSaving(true); setMsg(""); setError("");
    try {
      await saveSeverityRules(demo.id, rules);
      setIsDefault(false); setMsg("Saved."); onSaved?.();
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setSaving(false); }
  };
  const reset = async () => {
    setSaving(true); setMsg(""); setError("");
    try {
      const d = await resetSeverityRules(demo.id);
      setRules(d.rules?.length ? d.rules : DEFAULT_SEVERITY_RULES);
      setIsDefault(true); setMsg("Reset to defaults."); onSaved?.();
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setSaving(false); }
  };

  return (
    <main className="pf-tr">
      <ChecksSection />

      <section className="pf-panel">
        <div className="pf-dash-panel-head">
          <h2>Finding severity</h2>
          <span className="pf-dash-subtle">{isDefault ? "using defaults" : "customized"} · {demo.label}</span>
        </div>
        <p className="pf-hint" style={{ marginTop: 0 }}>
          How a finding's severity is derived. Each finding already carries a
          <strong> family</strong> (Policy / Integrity / Conformance) and an
          <strong> effect</strong> (what should have happened: block, approval,
          flag). These rules are evaluated top-down and the <em>first match
          wins</em> — drag priority with the ▲▼ controls. “Any” matches every
          value. Applies to this demo; keys on engine concepts only, never your
          data.
        </p>

        {status === "error" && <div className="pf-dash-feed-status error">Couldn’t load settings ({error}).</div>}

        {status !== "error" && (
          <table className="pf-dash-table pf-set-table">
            <thead>
              <tr><th style={{ width: 40 }}>#</th><th>Family</th><th>Effect</th><th>→ Severity</th><th style={{ width: 130 }}>Order</th></tr>
            </thead>
            <tbody>
              {rules.map((r, i) => (
                <tr key={i}>
                  <td className="muted">{i + 1}</td>
                  <td><Pick value={r.family ?? ""} options={FAMILY_OPTIONS} onChange={(v) => update(i, { family: v || null })} /></td>
                  <td><Pick value={r.effect ?? ""} options={EFFECT_OPTIONS} onChange={(v) => update(i, { effect: v || null })} /></td>
                  <td>
                    <select className="pf-set-pick" value={r.severity}
                            onChange={(e) => update(i, { severity: e.target.value as SeverityLevel })}>
                      {SEVERITY_ORDER.map((s) => <option key={s} value={s}>{SEVERITY_META[s].label}</option>)}
                    </select>
                    <span className={`pf-dash-chip ${SEVERITY_META[r.severity].tone}`} style={{ marginLeft: 8 }}>{SEVERITY_META[r.severity].label}</span>
                  </td>
                  <td>
                    <button className="pf-set-btn" type="button" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}>▲</button>
                    <button className="pf-set-btn" type="button" title="Move down" disabled={i === rules.length - 1} onClick={() => move(i, 1)}>▼</button>
                    <button className="pf-set-btn danger" type="button" title="Remove" disabled={rules.length <= 1} onClick={() => remove(i)}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="pf-set-actions">
          <button className="pf-dash-link" type="button" onClick={add} disabled={saving}>+ Add rule</button>
          <div style={{ flex: 1 }} />
          {msg && <span className="pf-dash-subtle">{msg}</span>}
          {error && status !== "error" && <span className="pf-dash-feed-status error" style={{ padding: 0 }}>{error}</span>}
          <button className="pf-dash-link" type="button" onClick={reset} disabled={saving}>Reset to defaults</button>
          <button className="pf-btn primary sm" type="button" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        </div>
      </section>

    </main>
  );
}
