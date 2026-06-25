// RuleProvenance — a rule's link back to the source policy document: the exact
// relevant excerpt (the cited phrase) plus the document section it came from.
// One renderer reused on every surface (decision trace, Policy Studio, graphs)
// so the SAME citation appears everywhere.
//
// Generic: `source` is an opaque bag {text, evidence, document, section}. Nothing
// here knows any domain vocabulary. Renders null when there's nothing to show.

export type RuleSource = {
  text?: string;       // full verbatim clause (carried for audit; not shown here)
  evidence?: string;   // the exact relevant excerpt to display
  document?: string;   // optional locator (document name/id)
  section?: string;    // optional locator (section / clause id)
};

export default function RuleProvenance({
  source,
}: {
  source?: RuleSource | null;
  collapsible?: boolean; // accepted for call-site compatibility; no longer used
}) {
  if (!source) return null;
  // Prefer the exact cited excerpt; fall back to the full clause only if there's
  // no excerpt for this rule.
  const excerpt = (source.evidence || source.text || "").trim();
  const locator = [source.document, source.section].filter(Boolean).join(" · ");
  if (!excerpt && !locator) return null;

  return (
    <div className="pf-prov">
      <div className="pf-prov-label">
        <span className="pf-prov-kicker">policy source</span>
        {locator && <span className="pf-prov-loc">{locator}</span>}
      </div>
      {excerpt && <blockquote className="pf-prov-quote">{excerpt}</blockquote>}
    </div>
  );
}
