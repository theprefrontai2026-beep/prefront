/*
 * useSeverityRules — the demo's finding-severity mapping, fetched once from
 * api-server (GET /api/settings/severity) and shared by every surface that
 * shows severity (Findings table, Overview roll-up). Falls back to the built-in
 * DEFAULT_SEVERITY_RULES if the endpoint is unreachable, so severity always
 * resolves. Settings.tsx uses `reload` after a save/reset to refresh consumers.
 */

import { useCallback, useEffect, useState } from "react";
import { getSeverityRules } from "../api";
import { DEFAULT_SEVERITY_RULES, type SeverityRule } from "../severity";

export function useSeverityRules(demoId: string, active = true): { rules: SeverityRule[]; isDefault: boolean; reload: () => void } {
  const [rules, setRules] = useState<SeverityRule[]>(DEFAULT_SEVERITY_RULES);
  const [isDefault, setIsDefault] = useState(true);

  const load = useCallback(() => {
    getSeverityRules(demoId)
      .then((d) => { setRules(d.rules?.length ? d.rules : DEFAULT_SEVERITY_RULES); setIsDefault(!!d.isDefault); })
      .catch(() => { setRules(DEFAULT_SEVERITY_RULES); setIsDefault(true); });
  }, [demoId]);

  useEffect(() => { load(); }, [load]);
  // Re-fetch when the consuming tab becomes visible again, so a save made in
  // Settings is reflected on next visit (all tabs stay mounted).
  useEffect(() => { if (active) load(); }, [active, load]);

  return { rules, isDefault, reload: load };
}
