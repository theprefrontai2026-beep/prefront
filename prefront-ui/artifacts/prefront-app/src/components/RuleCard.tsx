import { localTime } from "../util";
import type { Reviewer } from "../hooks/useReviewSync";

const DECISION_CLASS: Record<string, string> = {
  block: "decision-block",
  approval_required: "decision-approval",
  allow: "decision-allow",
  mask: "decision-mask",
  escalate: "decision-escalate",
};

function fmtValue(v: any): string {
  if (Array.isArray(v)) return v.join(", ");
  if (v === null || v === undefined) return "∅";
  return String(v);
}

const VCHECKS: [string, string][] = [
  ["executable", "exec"],
  ["source_grounded", "grounded"],
  ["semantic_valid", "semantic"],
  ["testable", "testable"],
];

interface Props {
  row: any;
  onApprove: (row: any) => void;
  onReject: (row: any) => void;
  busy: boolean;
  validation?: any;
  focusers?: Reviewer[];
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

export default function RuleCard({ row, onApprove, onReject, busy, validation, focusers = [], onMouseEnter, onMouseLeave }: Props) {
  const rule = row.rule || {};
  const effect = rule.effect || {};
  const status = row.review_status || "pending";
  const decided = status === "approved" || status === "rejected";
  const confidence = Math.round((row.confidence ?? rule.confidence ?? 0) * 100);

  return (
    <article
      className={`pf-rule-card status-${status}`}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {/* Reviewer focus indicators */}
      {focusers.length > 0 && (
        <div className="pf-rule-focusers">
          {focusers.map(r => (
            <span key={r.id} className="pf-focus-chip" style={{ background: r.color }}>
              {r.name}
            </span>
          ))}
        </div>
      )}

      <header className="pf-rule-head">
        <div className="pf-rule-title">
          <code className="pf-rule-key">{rule.rule_key}</code>
          <span className="pf-badge type">{rule.rule_type}</span>
        </div>
        <div className="pf-rule-meta">
          <span className={`pf-badge review-${status}`}>{status}</span>
          {row.created_at && (
            <span className="pf-rule-generated" title={`generated ${row.created_at} UTC`}>
              {localTime(row.created_at)}
            </span>
          )}
        </div>
      </header>

      {validation && (
        <div className="pf-rule-validation">
          {VCHECKS.map(([key, label]) => (
            <span key={key} className={`pf-vbadge ${validation[key] ? "ok" : "bad"}`}>
              {validation[key] ? "✓" : "✗"} {label}
            </span>
          ))}
        </div>
      )}

      <div className="pf-rule-body">
        <div className="pf-conditions">
          <span className="pf-label">When</span>
          <ul>
            {(rule.conditions || []).map((c: any, i: number) => (
              <li key={i}>
                <code>{c.field}</code> <em>{c.operator}</em>{" "}
                <code>{fmtValue(c.value)}</code>
              </li>
            ))}
          </ul>
        </div>

        <div className="pf-effect">
          <span className="pf-label">Then</span>
          <span className={`pf-badge decision pf-badge ${DECISION_CLASS[effect.decision] || ""}`}>
            {effect.decision}
          </span>
          {effect.approver_role && (
            <span className="pf-approver">→ {effect.approver_role}</span>
          )}
          {effect.restricted_fields?.length > 0 && (
            <span className="pf-restricted">
              restricts: {effect.restricted_fields.join(", ")}
            </span>
          )}
        </div>

        {effect.message && <p className="pf-message">{effect.message}</p>}

        {rule.applies_to_intents?.length > 0 && (
          <div className="pf-intents">
            {rule.applies_to_intents.map((i: string) => (
              <span key={i} className="pf-chip">{i}</span>
            ))}
          </div>
        )}

        {rule.source_evidence && (
          <blockquote className="pf-evidence">"{rule.source_evidence}"</blockquote>
        )}
      </div>

      <footer className="pf-rule-foot">
        <div className="pf-confidence" title={`confidence ${confidence}%`}>
          <div className="bar"><div className="fill" style={{ width: `${confidence}%` }} /></div>
          <span>{confidence}%</span>
        </div>
        <div className="pf-actions">
          <button className="pf-btn approve sm" disabled={decided || busy} onClick={() => onApprove(row)}>
            Approve
          </button>
          <button className="pf-btn reject sm" disabled={decided || busy} onClick={() => onReject(row)}>
            Reject
          </button>
        </div>
      </footer>
    </article>
  );
}
