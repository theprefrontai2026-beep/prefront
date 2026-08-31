import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildHref, currentLoc, setParams, useLoc } from "../lib/router";
import { TAB_PATH, navTo, onTab, policyDocHref, ruleHref } from "../routes";
import * as api from "../api";
import { localTime } from "../util";
import RuleCard from "./RuleCard";
import ClauseLedger from "./ClauseLedger";
import ValidationReport from "./ValidationReport";
import UnresolvedItems from "./UnresolvedItems";
import AuditLog from "./AuditLog";
import type { Reviewer, ReviewEvent } from "../hooks/useReviewSync";

interface Props {
  onRules: (rules: any[], domain: string) => void;
  schema: any;
  metrics: Record<string, string>;
  intents: string;
  setIntents: (v: string) => void;
  // Collaboration
  reviewers: Reviewer[];
  myId: string | null;
  onFocusRule: (ruleId: string | null) => void;
  broadcastRuleStatus: (ruleId: string, status: "approved" | "rejected", documentId?: string) => void;
  remoteRuleUpdates: ReviewEvent[];
}

const POLICY_TABS = ["rules", "ledger", "atoms", "validation", "unresolved", "audit", "activity"] as const;
type PolicyTab = typeof POLICY_TABS[number];

export default function PolicyStudio({
  onRules, schema, metrics, intents, setIntents,
  reviewers, myId, onFocusRule, broadcastRuleStatus, remoteRuleUpdates,
}: Props) {
  const [docs, setDocs] = useState<any[]>([]);
  // Both the open document and the sub-view live in the URL:
  //   /policy/<document_id>?tab=<sub-view>&rule=<rule_key>
  // so a reviewer can be sent straight to one rule of one document.
  const loc = useLoc();
  const here = onTab(loc.segs, "policy");
  // The open document is held in state AND mirrored to the URL, rather than
  // read straight off the path: every tab stays mounted, so the path belongs
  // to whichever tab is showing. State keeps the selection while you are
  // elsewhere; the sync effects below make the URL the authority whenever
  // this tab is the one on screen.
  const [docState, setDocState] = useState<string | null>(null);
  const urlDoc = here ? loc.segs[1] ?? null : null;
  const activeDocId = urlDoc ?? docState;
  const tab = here && (POLICY_TABS as readonly string[]).includes(loc.query.get("tab") ?? "")
    ? (loc.query.get("tab") as PolicyTab) : "rules";
  const linkedRule = here ? loc.query.get("rule") : null;
  // `rules` is the default, so it stays out of the URL — shared links stay short.
  // Both writers are no-ops on the URL unless this tab is the one showing.
  // Every tab body stays mounted, so an async callback here (the initial
  // document list resolving, a publish finishing) would otherwise rewrite the
  // address bar of whatever page the user is actually looking at.
  const setTab = useCallback((t: PolicyTab) => {
    if (onTab(currentLoc().segs, "policy")) setParams({ tab: t === "rules" ? null : t, rule: null }, { replace: true });
  }, []);
  const setActiveDocId = useCallback((id: string | null, opts: { replace?: boolean } = {}) => {
    setDocState(id);
    if (!onTab(currentLoc().segs, "policy")) return;   // state only; the effect below syncs the URL on arrival
    navTo(id ? policyDocHref(id, tab) : buildHref(TAB_PATH.policy, { tab: tab === "rules" ? null : tab }), opts);
  }, [tab]);
  // Coming back to a bare /policy with a document already open — put it back
  // in the URL so the address bar stays shareable.
  useEffect(() => {
    if (here && docState && !urlDoc) navTo(policyDocHref(docState, tab), { replace: true });
  }, [here, docState, urlDoc, tab]);
  // A shared /policy/<id> link (or Back onto one): adopt it as the selection.
  useEffect(() => { if (urlDoc && urlDoc !== docState) setDocState(urlDoc); }, [urlDoc, docState]);
  const [auditRefreshKey, setAuditRefreshKey] = useState(0);
  // Free-text filter over the extracted rules — matched against rule key, type,
  // conditions, effect, intents, message and source text (see filteredRules).
  const [ruleQuery, setRuleQuery] = useState("");

  /* Upload state */
  const [inputMode, setInputMode] = useState<"file" | "text">("file");
  const [textInput, setTextInput] = useState("");
  const [fileName, setFileName] = useState("policy.txt");
  const [domain, setDomain] = useState("");
  const [version, setVersion] = useState("1.0");
  // Provider is taken from the app's env default (LLM_PROVIDER, default openai);
  // the UI no longer selects it. Empty => backend resolves the env default.
  const provider = "";
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadStatus, setUploadStatus] = useState("");
  const [uploadError, setUploadError] = useState("");

  /* Extract state */
  const [extractBusy, setExtractBusy] = useState(false);
  const [extractStatus, setExtractStatus] = useState("");
  const [extractError, setExtractError] = useState("");
  const [extractProgress, setExtractProgress] = useState<{ completed: number; total: number } | null>(null);
  // Client-side activity trace of each extraction run — surfaced in the
  // "Extraction activity" tab so a customer can hand back a step-by-step log
  // (timings, per-clause progress, errors) when an extraction misbehaves.
  const [extractLog, setExtractLog] = useState<Array<{ t: number; level: "info" | "warn" | "error" | "done"; msg: string }>>([]);
  const logExtract = (level: "info" | "warn" | "error" | "done", msg: string) =>
    setExtractLog(prev => [...prev, { t: Date.now(), level, msg }].slice(-500));

  /* Rule action state */
  const [actionBusy, setActionBusy] = useState(false);

  /* Per-tab data */
  const [rules, setRules] = useState<any[]>([]);
  const [ledger, setLedger] = useState<any[]>([]);
  const [atoms, setAtoms] = useState<any[]>([]);
  const [validation, setValidation] = useState<any>(null);
  const [validationMap, setValidationMap] = useState<Record<string, any>>({});
  const [unresolved, setUnresolved] = useState<any[]>([]);
  const [profile, setProfile] = useState<any>(null);

  /* Classify/extract busy */
  const [classifyBusy, setClassifyBusy] = useState(false);
  const [atomsBusy, setAtomsBusy] = useState(false);
  const [validateBusy, setValidateBusy] = useState(false);
  const [subStatus, setSubStatus] = useState("");
  const [subError, setSubError] = useState("");

  /* Publish state */
  const [publishBusy, setPublishBusy] = useState(false);
  const [publishResult, setPublishResult] = useState<any>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  /* Collaboration: toast for remote events */
  const [colabToasts, setColabToasts] = useState<Array<{ id: number; text: string; color: string }>>([]);
  const toastSeq = useRef(0);

  const activeDoc = useMemo(() => docs.find(d => d.document_id === activeDocId) || null, [docs, activeDocId]);

  /* Apply remote rule status changes */
  useEffect(() => {
    if (!remoteRuleUpdates.length) return;
    const evt = remoteRuleUpdates[remoteRuleUpdates.length - 1];
    // Find reviewer for color
    const reviewer = reviewers.find(r => r.name === evt.by);
    const color = reviewer?.color || "var(--muted)";
    // Apply to local rules list
    setRules(prev => prev.map(r => {
      const key = r.rule?.rule_key || r.candidate_rule_id || r.id;
      return key === evt.ruleId ? { ...r, review_status: evt.status } : r;
    }));
    // Show a toast
    const tid = toastSeq.current++;
    const label = evt.status === "approved" ? "approved" : "rejected";
    setColabToasts(prev => [...prev, { id: tid, text: `${evt.by} ${label} ${evt.ruleId}`, color }]);
    setTimeout(() => setColabToasts(prev => prev.filter(t => t.id !== tid)), 3500);
  }, [remoteRuleUpdates]); // eslint-disable-line

  /* Reviewer focus map: ruleKey → Reviewer[] */
  const focusMap = useMemo(() => {
    const m: Record<string, Reviewer[]> = {};
    for (const r of reviewers) {
      if (r.id === myId || !r.focusedRuleId) continue;
      if (!m[r.focusedRuleId]) m[r.focusedRuleId] = [];
      m[r.focusedRuleId].push(r);
    }
    return m;
  }, [reviewers, myId]);

  /* Emit focus when hovering a rule card */
  const handleRuleFocus = useCallback((ruleId: string | null) => {
    onFocusRule(ruleId);
  }, [onFocusRule]);

  /* Load docs on mount */
  useEffect(() => {
    api.listDocuments().then(res => {
      const list = Array.isArray(res) ? res : (res.documents || []);
      setDocs(list);
      if (list.length && !activeDocId) setActiveDocId(list[0].document_id, { replace: true });
    }).catch(() => {});
  }, []); // eslint-disable-line

  /* Load rules when doc changes */
  useEffect(() => {
    if (!activeDocId) return;
    api.listRules(activeDocId).then(res => {
      const rs = Array.isArray(res) ? res : (res.candidate_rules || res.rules || []);
      setRules(rs);
      onRules(rs, domain || activeDoc?.domain || "");
    }).catch(() => {});
  }, [activeDocId]); // eslint-disable-line

  async function handleUpload() {
    setUploadError(""); setUploadStatus(""); setUploadBusy(true);
    try {
      let result: any;
      if (inputMode === "file") {
        const file = fileRef.current?.files?.[0];
        if (!file) throw new Error("Choose a file first");
        setUploadStatus("Uploading…");
        result = await api.uploadFile({ file, domain, version });
      } else {
        if (!textInput.trim()) throw new Error("Paste policy text first");
        setUploadStatus("Uploading…");
        result = await api.uploadText({ text: textInput.trim(), fileName, domain, version });
      }
      const doc = result.document || result;
      setDocs(prev => {
        const next = prev.filter(d => d.document_id !== doc.document_id);
        return [doc, ...next];
      });
      setActiveDocId(doc.document_id);
      if (doc.domain && !domain) setDomain(doc.domain);
      setUploadStatus(`Uploaded — ${doc.file_name || fileName}`);
      // upload returns only {document_id, status}; refresh the list to get the
      // full document record (file_name, domain, status, …).
      api.listDocuments()
        .then(r => setDocs(Array.isArray(r) ? r : (r.documents || [])))
        .catch(() => {});
    } catch (e: any) {
      setUploadError(String(e.message || e));
    } finally {
      setUploadBusy(false);
    }
  }

  async function handleExtract() {
    if (!activeDocId) return;
    setExtractError(""); setExtractStatus(""); setExtractProgress(null); setExtractBusy(true);
    const t0 = Date.now();
    const el = (s: number) => `${((s - t0) / 1000).toFixed(1)}s`;
    logExtract("info", `━━ New extraction run @ ${new Date(t0).toISOString()} ━━`);
    logExtract("info", `Document: ${activeDoc?.file_name || "?"} · id=${activeDocId} · v${activeDoc?.version ?? "?"} · status=${activeDoc?.status ?? "?"}`);
    logExtract("info", `LLM provider: ${provider || "env default (LLM_PROVIDER)"} · domain: ${domain || "none"}`);
    try {
      const knownIntents = intents.split(/[,\n]+/).map(s => s.trim()).filter(Boolean);
      const knownFields = schema?.catalog
        ? (schema.catalog.tables || []).flatMap((t: any) => (t.columns || []).map((c: any) => c.name))
        : [];
      logExtract("info", `Known intents (${knownIntents.length}): ${knownIntents.length ? knownIntents.join(", ") : "—"}`);
      logExtract("info", `Known fields (${knownFields.length}): ${knownFields.length ? knownFields.slice(0, 12).join(", ") + (knownFields.length > 12 ? `, …+${knownFields.length - 12}` : "") : "— (no schema connected)"}`);
      logExtract("info", `POST extract-rules/start …`);
      const started = await api.startExtractRules(activeDocId, { provider, domain, knownIntents, knownFields });
      const total = started.total || 0;
      setExtractProgress({ completed: 0, total });
      logExtract("info", `Job accepted (${el(Date.now())}) — ${total} clause${total !== 1 ? "s" : ""} queued for extraction`);
      if (total === 0) logExtract("warn", `Zero clauses to process — the document may have produced no numbered sections`);

      // Poll until the background job (skill-builder/skillbuilder/api.py's
      // extract-rules/start + /progress) reports done or error. The progress
      // bar below is driven by extractProgress; extractStatus is reserved
      // for the final "N rules extracted" message so it never shows a "✓"
      // while still running.
      let res: any;
      let lastCompleted = -1;
      let polls = 0;
      for (;;) {
        await new Promise(r => setTimeout(r, 400));
        polls++;
        const progress = await api.getExtractRulesProgress(activeDocId);
        setExtractProgress({ completed: progress.completed, total: progress.total });
        if (progress.completed !== lastCompleted) {
          const pct = progress.total ? Math.round((progress.completed / progress.total) * 100) : 0;
          logExtract("info", `Progress: clause ${progress.completed}/${progress.total} (${pct}%) · status=${progress.status} · ${el(Date.now())}`);
          lastCompleted = progress.completed;
        }
        if (progress.status === "running") continue;
        if (progress.status === "error") {
          logExtract("error", `Backend reported status=error after ${polls} poll${polls !== 1 ? "s" : ""}: ${progress.error || "extraction failed"}`);
          throw new Error(progress.error || "extraction failed");
        }
        logExtract("info", `Extraction finished with status=${progress.status} after ${polls} poll${polls !== 1 ? "s" : ""} (${el(Date.now())})`);
        res = progress.result;
        break;
      }

      const errs = res.errors || [];
      logExtract("info", `Result summary: keys=[${Object.keys(res || {}).join(", ") || "—"}] · reported errors=${errs.length}`);
      // extract-rules returns only a count; the rules themselves come from the
      // candidate-rules list endpoint (keyed `candidate_rules`).
      logExtract("info", `GET candidate-rules …`);
      const listed = await api.listRules(activeDocId);
      const rs = Array.isArray(listed) ? listed : (listed.candidate_rules || listed.rules || []);
      setRules(rs);
      onRules(rs, domain);
      const n = rs.length;
      // Breakdown by the effect decision so the log shows what KIND of rules came back.
      const byDecision: Record<string, number> = {};
      for (const r of rs) {
        const d = r.rule?.effect?.decision || "unknown";
        byDecision[d] = (byDecision[d] || 0) + 1;
      }
      const breakdown = Object.entries(byDecision).map(([k, v]) => `${k}:${v}`).join(", ");
      logExtract("info", `Fetched ${n} candidate rule${n !== 1 ? "s" : ""}${breakdown ? ` — ${breakdown}` : ""}`);
      if (n === 0) logExtract("warn", `No candidate rules were produced from ${total} clause${total !== 1 ? "s" : ""}`);
      setExtractStatus(`${n} rule${n !== 1 ? "s" : ""} extracted${errs.length ? ` (${errs.length} errors)` : ""}`);
      for (const e of errs) logExtract("warn", `Extraction error: ${typeof e === "string" ? e : JSON.stringify(e)}`);
      logExtract("done", `✓ Done — ${n} rule${n !== 1 ? "s" : ""} extracted${errs.length ? `, ${errs.length} error${errs.length !== 1 ? "s" : ""}` : ""} in ${el(Date.now())}`);
      setTab("rules");
    } catch (e: any) {
      setExtractError(String(e.message || e));
      logExtract("error", `✗ Run failed after ${el(Date.now())}: ${String(e.message || e)}`);
    } finally {
      setExtractBusy(false);
      setExtractProgress(null);
    }
  }

  async function handleApprove(row: any) {
    setActionBusy(true);
    try {
      const res = await api.approveRule(row.candidate_rule_id || row.id, { version });
      const updated = res.candidate_rule || { ...row, review_status: "approved" };
      setRules(prev => prev.map(r => r.candidate_rule_id === (row.candidate_rule_id || row.id) ? updated : r));
      // Broadcast to co-reviewers (include documentId so server can persist the audit entry)
      broadcastRuleStatus(row.rule?.rule_key || row.candidate_rule_id, "approved", activeDocId ?? undefined);
      setAuditRefreshKey(k => k + 1);
    } catch (e: any) {
      alert("Approve failed: " + e.message);
    } finally {
      setActionBusy(false);
    }
  }

  async function handleReject(row: any) {
    const reason = window.prompt("Rejection reason");
    if (!reason) return;
    setActionBusy(true);
    try {
      const res = await api.rejectRule(row.candidate_rule_id || row.id, reason);
      const updated = res.candidate_rule || { ...row, review_status: "rejected" };
      setRules(prev => prev.map(r => r.candidate_rule_id === (row.candidate_rule_id || row.id) ? updated : r));
      // Broadcast to co-reviewers (include documentId so server can persist the audit entry)
      broadcastRuleStatus(row.rule?.rule_key || row.candidate_rule_id, "rejected", activeDocId ?? undefined);
      setAuditRefreshKey(k => k + 1);
    } catch (e: any) {
      alert("Reject failed: " + e.message);
    } finally {
      setActionBusy(false);
    }
  }

  async function handleClassify() {
    if (!activeDocId) return;
    setSubError(""); setSubStatus("Classifying clauses…"); setClassifyBusy(true);
    try {
      await api.classifyClauses(activeDocId, { provider });
      const ledgerRes = await api.getClauseLedger(activeDocId);
      setLedger(Array.isArray(ledgerRes) ? ledgerRes : (ledgerRes.entries || ledgerRes.clauses || []));
      setSubStatus("Clauses classified");
      setTab("ledger");
    } catch (e: any) {
      setSubError(String(e.message || e));
    } finally {
      setClassifyBusy(false);
    }
  }

  async function handleExtractAtoms() {
    if (!activeDocId) return;
    setSubError(""); setSubStatus("Extracting atoms…"); setAtomsBusy(true);
    try {
      await api.extractAtoms(activeDocId, { provider });
      // extract-policy-atoms returns only a count; fetch the atoms list.
      const listed = await api.listAtoms(activeDocId);
      setAtoms(Array.isArray(listed) ? listed : (listed.atoms || []));
      setSubStatus("Atoms extracted");
      setTab("atoms");
    } catch (e: any) {
      setSubError(String(e.message || e));
    } finally {
      setAtomsBusy(false);
    }
  }

  async function handleValidate() {
    if (!activeDocId) return;
    setSubError(""); setSubStatus("Validating…"); setValidateBusy(true);
    try {
      const declaredParams = schema?.catalog
        ? (schema.catalog.tables || []).flatMap((t: any) => (t.columns || []).map((c: any) => c.name))
        : [];
      const metricsList = Object.keys(metrics || {});
      const res = await api.validateDocument(activeDocId, { declaredParams, metrics: metricsList });
      setValidation(res);
      const vm: Record<string, any> = {};
      for (const r of res.rule_results || []) vm[r.rule_key] = r;
      setValidationMap(vm);

      const uRes = await api.listUnresolved(activeDocId);
      setUnresolved(Array.isArray(uRes) ? uRes : (uRes.unresolved_items || uRes.items || []));
      setSubStatus("Validation complete");
      setTab("validation");
    } catch (e: any) {
      setSubError(String(e.message || e));
    } finally {
      setValidateBusy(false);
    }
  }

  async function handleDeleteDoc(docId: string) {
    if (!window.confirm("Delete this document and all its rules?")) return;
    await api.deleteDocument(docId).catch(() => {});
    setDocs(prev => prev.filter(d => d.document_id !== docId));
    if (activeDocId === docId) {
      const remaining = docs.filter(d => d.document_id !== docId);
      setActiveDocId(remaining[0]?.document_id || null, { replace: true });
    }
  }

  async function refreshRules() {
    if (!activeDocId) return;
    const res = await api.listRules(activeDocId);
    const rs = Array.isArray(res) ? res : (res.candidate_rules || res.rules || []);
    setRules(rs);
    onRules(rs, domain);
  }

  async function handleApproveAll() {
    if (!activeDocId) return;
    setBulkBusy(true); setExtractError("");
    try {
      const res = await api.approveAllRules(activeDocId, { version });
      await refreshRules();
      const n = res.approved ?? 0;
      setExtractStatus(`Approved ${n} rule${n !== 1 ? "s" : ""}${res.errors?.length ? ` (${res.errors.length} skipped)` : ""}`);
    } catch (e: any) {
      setExtractError(String(e.message || e));
    } finally {
      setBulkBusy(false);
    }
  }

  async function handleResetApprovals() {
    if (!activeDocId) return;
    if (!window.confirm("Reset all rules back to pending review?")) return;
    setBulkBusy(true); setExtractError(""); setPublishResult(null);
    try {
      const res = await api.resetApprovals(activeDocId);
      await refreshRules();
      const n = res.reset ?? 0;
      setExtractStatus(`Reset ${n} rule${n !== 1 ? "s" : ""} to pending`);
    } catch (e: any) {
      setExtractError(String(e.message || e));
    } finally {
      setBulkBusy(false);
    }
  }

  async function handlePublish() {
    if (!activeDocId) return;
    setPublishBusy(true); setPublishResult(null);
    try {
      const res = await api.publishSkill(activeDocId, {
        documentId: activeDocId,
        name: activeDoc?.file_name || "policy",
        domain,
      });
      setPublishResult(res);
    } catch (e: any) {
      setPublishResult({ error: String(e.message || e) });
    } finally {
      setPublishBusy(false);
    }
  }

  async function handleResolveUnresolved(id: string, status: string) {
    try {
      await api.resolveUnresolved(id, { status });
      setUnresolved(prev => prev.map(u => u.unresolved_id === id ? { ...u, status } : u));
    } catch (e: any) {
      alert("Resolve failed: " + e.message);
    }
  }

  async function handleShowLedger() {
    if (!activeDocId) return;
    setTab("ledger");
    try {
      const res = await api.getClauseLedger(activeDocId);
      setLedger(Array.isArray(res) ? res : (res.entries || res.clauses || []));
    } catch { /* empty */ }
  }

  async function handleShowAtoms() {
    if (!activeDocId) return;
    setTab("atoms");
    try {
      const res = await api.listAtoms(activeDocId);
      setAtoms(Array.isArray(res) ? res : (res.atoms || []));
    } catch { /* empty */ }
  }

  async function handleShowProfile() {
    if (!activeDocId) return;
    setTab("ledger");
    try {
      const res = await api.getProfile(activeDocId);
      setProfile(res);
    } catch { /* empty */ }
  }

  const summaryApproved = rules.filter(r => r.review_status === "approved").length;
  const summaryPending = rules.filter(r => r.review_status === "pending").length;
  const summaryRejected = rules.filter(r => r.review_status === "rejected").length;

  /* Online co-reviewers (not self) for summary */
  const coReviewers = reviewers.filter(r => r.id !== myId);

  /* Free-text filter over the extracted rules. Builds a lower-cased haystack
     from every reviewer-relevant field so a search matches on rule key, type,
     condition fields/values, decision, intents, message or the cited source. */
  const filteredRules = useMemo(() => {
    const q = ruleQuery.trim().toLowerCase();
    if (!q) return rules;
    const terms = q.split(/\s+/);
    return rules.filter((r: any) => {
      const rule = r.rule || {};
      const effect = rule.effect || {};
      const hay = [
        rule.rule_key, rule.rule_type, r.review_status,
        effect.decision, effect.approver_role, effect.message,
        ...(effect.restricted_fields || []),
        ...(rule.applies_to_intents || []),
        ...(rule.conditions || []).flatMap((c: any) => [c.field, c.operator, Array.isArray(c.value) ? c.value.join(" ") : c.value]),
        // Match the citation shown ON the card: the rule-specific excerpt
        // (`source_evidence`) and its section locator — NOT `source_text`, which
        // is the whole policy section (invisible here and shared across every
        // rule in it, so it both hid card-visible words and over-matched).
        rule.source_evidence, rule.source?.section, rule.source?.document,
      ].filter(Boolean).join(" ").toLowerCase();
      return terms.every(t => hay.includes(t));
    });
  }, [rules, ruleQuery]);

  return (
    <main className="pf-studio">
      {/* Collaboration toast tray */}
      {colabToasts.length > 0 && (
        <div style={{
          position: "fixed", bottom: 28, right: 28, zIndex: 999,
          display: "flex", flexDirection: "column", gap: 8, pointerEvents: "none",
        }}>
          {colabToasts.map(t => (
            <div key={t.id} style={{
              background: "var(--card)", border: "1px solid var(--line)",
              borderLeft: `3px solid ${t.color}`,
              borderRadius: "var(--radius)", padding: "10px 16px",
              fontSize: 13, color: "var(--ink)", boxShadow: "var(--shadow)",
              animation: "pf-fadein 0.2s ease",
            }}>
              {t.text}
            </div>
          ))}
        </div>
      )}

      {/* Explorer sidebar */}
      <aside className="pf-explorer">
        <div className="pf-explorer-head">
          Documents
          <button className="pf-explorer-new" onClick={() => setTab("rules")}>+ New</button>
        </div>
        {docs.length === 0
          ? <div className="pf-explorer-empty">No documents yet</div>
          : (
            <ul className="pf-explorer-list">
              {docs.map(d => (
                <li key={d.document_id} className={`pf-explorer-item ${d.document_id === activeDocId ? "active" : ""}`}>
                  <div className="pf-doc-open" onClick={() => { setActiveDocId(d.document_id); setTab("rules"); }}>
                    <span className="pf-doc-name">{d.file_name}</span>
                    <span className="pf-doc-meta">
                      <span className="pf-doc-ver">v{d.version}</span>
                      <span className={`pf-doc-status s-${d.status}`}>{d.status}</span>
                    </span>
                  </div>
                  <button className="pf-doc-remove" title="Delete" onClick={() => handleDeleteDoc(d.document_id)}>×</button>
                </li>
              ))}
            </ul>
          )}

        {/* Co-reviewer presence in sidebar */}
        {coReviewers.length > 0 && (
          <div style={{ padding: "10px 16px", borderTop: "1px solid var(--line)" }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8 }}>
              Also reviewing
            </div>
            {coReviewers.map(r => (
              <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5, fontSize: 12 }}>
                <div style={{
                  width: 22, height: 22, borderRadius: "50%", background: r.color,
                  display: "grid", placeItems: "center",
                  fontSize: 10, fontWeight: 700, color: "white", flexShrink: 0,
                }}>{r.name[0]}</div>
                <span style={{ color: "var(--ink-soft)" }}>{r.name}</span>
                {r.focusedRuleId && (
                  <span style={{ fontSize: 10, color: "var(--muted)" }}>viewing a rule</span>
                )}
              </div>
            ))}
          </div>
        )}
      </aside>

      {/* Main area */}
      <div className="pf-studio-main">
        {/* Upload panel */}
        <div className="pf-panel">
          <h2><span className="pf-step-badge">1</span>Upload policy document</h2>
          <div className="pf-tabs">
            <button className={`pf-tab ${inputMode === "file" ? "active" : ""}`} onClick={() => setInputMode("file")}>File</button>
            <button className={`pf-tab ${inputMode === "text" ? "active" : ""}`} onClick={() => setInputMode("text")}>Paste text</button>
          </div>

          {inputMode === "file"
            ? <input type="file" ref={fileRef} accept=".txt,.pdf,.md,.docx,.json" />
            : (
              <div className="pf-fields">
                <label style={{ gridColumn: "1 / -1" }}>
                  Policy text
                  <textarea value={textInput} onChange={e => setTextInput(e.target.value)} rows={8}
                    placeholder="Paste the full text of your data policy, governance document, or rulebook here…" />
                </label>
                <label>File name<input value={fileName} onChange={e => setFileName(e.target.value)} /></label>
              </div>
            )}

          <div className="pf-fields">
            <label>Version<input value={version} onChange={e => setVersion(e.target.value)} /></label>
          </div>

          <div className="pf-publish-row">
            <button className="pf-btn primary" onClick={handleUpload} disabled={uploadBusy}>
              {uploadBusy ? "Uploading…" : "Upload"}
            </button>
            {/* Extract sits right next to Upload — it acts on the active document
                (the one just uploaded, or the one selected in the sidebar), and
                is disabled until there is one. */}
            <button className="pf-btn primary" onClick={handleExtract} disabled={!activeDoc || extractBusy || actionBusy}
              title={activeDoc ? "" : "Upload or select a document first"}>
              {extractBusy ? "Extracting…" : "Extract rules"}
            </button>
            {uploadStatus && <span className="pf-status">✓ {uploadStatus}</span>}
            {uploadError && <span className="pf-error">{uploadError}</span>}
            {extractStatus && <span className="pf-status">✓ {extractStatus}</span>}
            {extractError && <span className="pf-error">{extractError}</span>}
          </div>

          {extractBusy && extractProgress && extractProgress.total > 0 && (() => {
            const pct = Math.min(100, Math.round((extractProgress.completed / extractProgress.total) * 100));
            return (
              <div className="pf-progress" role="progressbar"
                   aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <div className="pf-progressbar">
                  <div className="pf-progressbar-fill" style={{ width: `${pct}%` }} />
                </div>
                <div className="pf-progress-step active">
                  <span className="pf-progress-icon"><span className="pf-spin" /></span>
                  <span>Extracting clause {extractProgress.completed} of {extractProgress.total} — {pct}%</span>
                </div>
              </div>
            );
          })()}
        </div>

        {activeDoc && (
          <>
            {(subStatus || subError) && (
              <div className="pf-panel" style={{ paddingTop: 10, paddingBottom: 10 }}>
                {subStatus && <span className="pf-status">✓ {subStatus}</span>}
                {subError && <span className="pf-error" style={{ marginLeft: 12 }}>{subError}</span>}
              </div>
            )}

            {/* Sub-nav */}
            <div className="pf-panel" style={{ paddingTop: 0, paddingBottom: 0 }}>
              <div className="pf-sub-nav">
                <button className={`pf-sub-tab ${tab === "rules" ? "active" : ""}`} onClick={() => setTab("rules")}>
                  Rules {rules.length > 0 && <span className="pf-pill" style={{ marginLeft: 6 }}>{rules.length}</span>}
                </button>
                <button className={`pf-sub-tab ${tab === "ledger" ? "active" : ""}`} onClick={handleShowLedger}>
                  Clause ledger
                </button>
                <button className={`pf-sub-tab ${tab === "unresolved" ? "active" : ""}`} onClick={() => setTab("unresolved")}>
                  Unresolved {unresolved.filter(u => u.status === "open").length > 0 &&
                    <span className="pf-pill rejected" style={{ marginLeft: 6 }}>{unresolved.filter(u => u.status === "open").length}</span>}
                </button>
                <div className="pf-sub-nav-spacer" />
                <button className="pf-sub-tab" onClick={handleShowProfile}>Profile</button>
                <button
                  className={`pf-sub-tab ${tab === "audit" ? "active" : ""}`}
                  onClick={() => { setTab("audit"); setAuditRefreshKey(k => k + 1); }}
                >
                  Audit log
                </button>
                <button className={`pf-sub-tab ${tab === "activity" ? "active" : ""}`} onClick={() => setTab("activity")}>
                  Extraction activity {extractLog.length > 0 && <span className="pf-pill" style={{ marginLeft: 6 }}>{extractLog.length}</span>}
                </button>
              </div>
            </div>

            {/* Tab content */}
            <div className="pf-panel">
              {tab === "rules" && (
                <>
                  <div className="pf-summary">
                    <span className="pf-pill">{rules.length} total</span>
                    {summaryApproved > 0 && <span className="pf-pill approved">{summaryApproved} approved</span>}
                    {summaryPending > 0 && <span className="pf-pill pending">{summaryPending} pending</span>}
                    {summaryRejected > 0 && <span className="pf-pill rejected">{summaryRejected} rejected</span>}
                    {coReviewers.length > 0 && (
                      <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
                        {coReviewers.map(r => (
                          <span key={r.id} title={r.name} style={{
                            width: 22, height: 22, borderRadius: "50%",
                            background: r.color, display: "grid", placeItems: "center",
                            fontSize: 10, fontWeight: 700, color: "white",
                          }}>{r.name[0]}</span>
                        ))}
                        <span style={{ fontSize: 12, color: "var(--muted)" }}>reviewing live</span>
                      </span>
                    )}
                  </div>

                  {/* Bulk actions — publish + approve-all/reset, kept at the top
                      so the primary actions are reachable without scrolling. */}
                  {rules.length > 0 && (
                    <div className="pf-publish-row" style={{ marginBottom: 16, flexWrap: "wrap", gap: 8 }}>
                      <button className="pf-btn primary" onClick={handlePublish}
                        disabled={publishBusy || bulkBusy || summaryApproved === 0}>
                        {publishBusy ? "Publishing…" : `Publish ${summaryApproved} approved rule${summaryApproved !== 1 ? "s" : ""}`}
                      </button>
                      <button className="pf-btn" onClick={handleApproveAll}
                        disabled={bulkBusy || publishBusy || summaryPending === 0}>
                        {bulkBusy ? "Working…" : `Approve all${summaryPending ? ` (${summaryPending})` : ""}`}
                      </button>
                      <button className="pf-btn" onClick={handleResetApprovals}
                        disabled={bulkBusy || publishBusy || (summaryApproved + summaryRejected) === 0}>
                        Reset approvals
                      </button>
                    </div>
                  )}

                  {publishResult && (
                    <div className="pf-publish-result" style={{ marginBottom: 16 }}>
                      {publishResult.error
                        ? <span className="pf-error">{publishResult.error}</span>
                        : (
                          <>
                            ✓ Published — skill_id: <code>{publishResult.skill_id}</code>
                            {publishResult.published_rule_keys && (
                              <ul>{publishResult.published_rule_keys.map((k: string) => <li key={k}><code>{k}</code></li>)}</ul>
                            )}
                          </>
                        )}
                    </div>
                  )}

                  {/* Search across every extracted rule — key, type, conditions,
                      decision, intents, message, source. */}
                  {rules.length > 0 && (
                    <div className="pf-rule-search" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                      <input
                        value={ruleQuery}
                        onChange={e => setRuleQuery(e.target.value)}
                        placeholder="Search rules — key, field, decision, intent, source…"
                        style={{ flex: 1 }}
                      />
                      {ruleQuery && (
                        <>
                          <span style={{ fontSize: 12, color: "var(--muted)", whiteSpace: "nowrap" }}>
                            {filteredRules.length} of {rules.length}
                          </span>
                          <button className="pf-btn" onClick={() => setRuleQuery("")}>Clear</button>
                        </>
                      )}
                    </div>
                  )}

                  {rules.length === 0
                    ? <p className="pf-hint">No rules yet — click "Extract rules" above to generate candidates.</p>
                    : filteredRules.length === 0
                    ? <p className="pf-hint">No rules match “{ruleQuery}”.</p>
                    : (
                      <div className="pf-rules">
                        {filteredRules.map((r: any) => {
                          const ruleKey = r.rule?.rule_key || r.candidate_rule_id || r.id;
                          return (
                            <RuleCard
                              key={r.candidate_rule_id || r.id}
                              row={r}
                              onApprove={handleApprove}
                              onReject={handleReject}
                              busy={actionBusy}
                              validation={validationMap[r.rule?.rule_key]}
                              focusers={focusMap[ruleKey] || []}
                              onMouseEnter={() => handleRuleFocus(ruleKey)}
                              onMouseLeave={() => handleRuleFocus(null)}
                              shareHref={ruleHref(activeDocId, ruleKey)}
                              highlighted={!!linkedRule && linkedRule === ruleKey}
                            />
                          );
                        })}
                      </div>
                    )}
                </>
              )}

              {tab === "ledger" && (
                <>
                  {profile && (
                    <div style={{ marginBottom: 16 }}>
                      <h3>Document profile</h3>
                      <pre className="pf-profile-json">{JSON.stringify(profile, null, 2)}</pre>
                    </div>
                  )}
                  <ClauseLedger entries={ledger} />
                </>
              )}

              {tab === "atoms" && (
                <>
                  {atoms.length === 0
                    ? <p className="pf-hint">No atoms yet — click "Extract atoms" above.</p>
                    : (
                      <ul className="pf-atom-list">
                        {atoms.map((a: any, i: number) => (
                          <li key={i}>
                            {a.rule_key && <span className="pf-rule-key-sm">{a.rule_key}</span>}
                            {a.statement}
                            {a.source_text && <blockquote className="pf-evidence">{a.source_text}</blockquote>}
                          </li>
                        ))}
                      </ul>
                    )}
                </>
              )}

              {tab === "validation" && <ValidationReport report={validation} />}
              {tab === "unresolved" && (
                <UnresolvedItems items={unresolved} onResolve={handleResolveUnresolved} />
              )}
              {tab === "audit" && activeDocId && (
                <AuditLog documentId={activeDocId} refreshKey={auditRefreshKey} />
              )}

              {tab === "activity" && (
                <>
                  <div className="pf-summary" style={{ alignItems: "center" }}>
                    <h3 style={{ margin: 0 }}>Extraction activity</h3>
                    <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                      <button className="pf-btn" disabled={extractLog.length === 0}
                        onClick={() => navigator.clipboard?.writeText(
                          extractLog.map(l => `${new Date(l.t).toISOString()} [${l.level}] ${l.msg}`).join("\n"))}>
                        Copy log
                      </button>
                      <button className="pf-btn" disabled={extractLog.length === 0} onClick={() => setExtractLog([])}>
                        Clear
                      </button>
                    </span>
                  </div>
                  <p className="pf-hint">
                    A step-by-step, client-side trace of each “Extract rules” run — timings, per-clause
                    progress and errors. Handy to copy back to Prefront when an extraction looks wrong.
                  </p>
                  {extractLog.length === 0
                    ? <p className="pf-hint">No extraction runs yet — click “Extract rules” to populate this log.</p>
                    : (
                      <ul className="pf-extract-activity" style={{ listStyle: "none", margin: 0, padding: 0, fontFamily: "var(--mono, monospace)", fontSize: 12 }}>
                        {extractLog.map((l, i) => (
                          <li key={i} style={{
                            display: "flex", gap: 10, padding: "4px 0",
                            borderTop: i ? "1px solid var(--line)" : "none",
                            color: l.level === "error" ? "var(--danger, #c0392b)"
                              : l.level === "warn" ? "var(--warn, #b7791f)"
                              : l.level === "done" ? "var(--ok, #2f855a)" : "var(--ink-soft)",
                          }}>
                            <span style={{ color: "var(--muted)", flexShrink: 0 }}>{localTime(new Date(l.t).toISOString())}</span>
                            <span style={{ textTransform: "uppercase", fontSize: 10, opacity: 0.7, flexShrink: 0, width: 42 }}>{l.level}</span>
                            <span style={{ whiteSpace: "pre-wrap" }}>{l.msg}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                </>
              )}
            </div>
          </>
        )}

        {!activeDoc && docs.length === 0 && (
          <div className="pf-panel">
            <p className="pf-hint">Upload a policy document to get started. Prefront will extract, review and publish governance rules from it.</p>
          </div>
        )}
      </div>
    </main>
  );
}
