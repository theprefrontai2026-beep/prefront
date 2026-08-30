/*
 * Settings — the "under the hood" configuration surface, reached from the gear
 * in the sidebar (not part of the numbered pipeline). Its first (and, for now,
 * only) section is the Finding Severity mapping: a customer-editable ordered
 * rule-list that derives each finding's severity from its family + effect
 * (first-match-wins). Persisted per demo via api-server (GET/PUT/DELETE
 * /api/settings/severity). Domain-neutral by construction — rules key on engine
 * concepts only.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { DemoConfig } from "../demos";
import { getSeverityRules, saveSeverityRules, resetSeverityRules } from "../api";
import {
  DEFAULT_SEVERITY_RULES, SEVERITY_META, SEVERITY_ORDER,
  FAMILY_OPTIONS, EFFECT_OPTIONS, severityOf,
  type SeverityLevel, type SeverityRule,
} from "../severity";

// A tiny worked example so an editor can see how the current ordering resolves
// the cases the mapping is really about — mirrors the demo's real finding shapes.
const PREVIEW: { label: string; family: string; effect: string }[] = [
  { label: "Blocking policy violation (F1 · block)",     family: "family1", effect: "block" },
  { label: "Approval-gated action (F1 · approval)",       family: "family1", effect: "approval_required" },
  { label: "Integrity invariant, blocking (F2 · block)",  family: "family2", effect: "block" },
  { label: "Integrity invariant, flagged (F2 · flag)",    family: "family2", effect: "flag" },
  { label: "Conformance drift (F3 · flag)",               family: "family3", effect: "flag" },
];

function Pick({ value, options, onChange }: {
  value: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
}) {
  return (
    <select className="pf-set-pick" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
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

  const preview = useMemo(
    () => PREVIEW.map((p) => ({ ...p, sev: severityOf(p, rules) })),
    [rules],
  );

  return (
    <main className="pf-tr">
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

      <section className="pf-panel" style={{ marginTop: 14 }}>
        <div className="pf-dash-panel-head"><h2>Preview</h2></div>
        <p className="pf-hint" style={{ marginTop: 0 }}>How the current ordering rates representative findings.</p>
        <table className="pf-dash-table pf-set-table">
          <thead><tr><th>Example finding</th><th style={{ width: 120 }}>Severity</th></tr></thead>
          <tbody>
            {preview.map((p, i) => (
              <tr key={i}>
                <td>{p.label}</td>
                <td><span className={`pf-dash-chip ${SEVERITY_META[p.sev].tone}`}>{SEVERITY_META[p.sev].label}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
