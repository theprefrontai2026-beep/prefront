import { useEffect, useState, useCallback } from "react";
import { fetchAuditLog } from "../api";

interface AuditEntry {
  id: number;
  documentId: string;
  ruleKey: string;
  action: string;
  reviewerName: string;
  reviewerColor: string | null;
  before: unknown;
  after: unknown;
  note: string | null;
  createdAt: string;
}

const ACTION_STYLE: Record<string, { label: string; color: string }> = {
  approved:  { label: "approved",  color: "#4a6741" },
  rejected:  { label: "rejected",  color: "#8f4a38" },
  extracted: { label: "extracted", color: "#8a6420" },
};

function ago(ts: string) {
  const diff = Date.now() - new Date(ts).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60)  return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts).toLocaleDateString();
}

interface Props {
  documentId: string;
  /** Pass a counter that increments after each approve/reject to trigger a refresh */
  refreshKey?: number;
}

export default function AuditLog({ documentId, refreshKey }: Props) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (!documentId) return;
    setLoading(true); setError("");
    fetchAuditLog(documentId)
      .then(res => {
        const list: AuditEntry[] = Array.isArray(res) ? res : (res.entries || []);
        setEntries(list);
      })
      .catch(e => setError(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [documentId]);

  useEffect(() => { load(); }, [load, refreshKey]);

  if (!documentId) return <p className="pf-hint">Open a document first.</p>;

  return (
    <div className="pf-audit">
      <div className="pf-audit-header">
        <h3>Audit log</h3>
        <button className="pf-btn sm" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {error && <span className="pf-error">{error}</span>}

      {!loading && entries.length === 0 && (
        <p className="pf-hint">No audit entries yet — approve or reject a rule to create the first entry.</p>
      )}

      {entries.length > 0 && (
        <table className="pf-audit-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Reviewer</th>
              <th>Rule</th>
              <th>Action</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(e => {
              const style = ACTION_STYLE[e.action] ?? { label: e.action, color: "var(--muted)" };
              return (
                <tr key={e.id}>
                  <td className="pf-audit-ts" title={new Date(e.createdAt).toLocaleString()}>
                    {ago(e.createdAt)}
                  </td>
                  <td>
                    <span className="pf-audit-reviewer">
                      <span
                        className="pf-audit-dot"
                        style={{ background: e.reviewerColor ?? "var(--muted)" }}
                      />
                      {e.reviewerName}
                    </span>
                  </td>
                  <td><code className="pf-rule-key">{e.ruleKey}</code></td>
                  <td>
                    <span
                      className="pf-badge"
                      style={{
                        background: style.color + "22",
                        color: style.color,
                        border: `1px solid ${style.color}44`,
                      }}
                    >
                      {style.label}
                    </span>
                  </td>
                  <td className="pf-audit-note">{e.note ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
