async function jsonOrThrow(res: Response) {
  const text = await res.text();
  let body: any;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text };
  }
  if (!res.ok) {
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return body;
}

export function listDocuments() {
  return fetch("/design/skills/documents").then(jsonOrThrow);
}

export function deleteDocument(documentId: string) {
  return fetch(`/design/skills/documents/${documentId}`, { method: "DELETE" }).then(jsonOrThrow);
}

export function uploadText({ text, fileName, domain, version }: { text: string; fileName: string; domain: string; version: string }) {
  return fetch("/design/skills/documents/upload", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text, file_name: fileName, domain, version }),
  }).then(jsonOrThrow);
}

export function uploadFile({ file, domain, version }: { file: File; domain: string; version: string }) {
  const form = new FormData();
  form.append("file", file);
  if (domain) form.append("domain", domain);
  if (version) form.append("version", version);
  return fetch("/design/skills/documents/upload", {
    method: "POST",
    body: form,
  }).then(jsonOrThrow);
}

export function extractRules(documentId: string, { provider, domain, knownIntents, knownFields, knownRoles }: any = {}) {
  return fetch(`/design/skills/documents/${documentId}/extract-rules`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      provider: provider || null,
      domain: domain || null,
      known_intents: knownIntents || [],
      known_fields: knownFields || [],
      known_roles: knownRoles || [],
    }),
  }).then(jsonOrThrow);
}

// Same body as extractRules, but returns immediately once the background job
// starts ({document_id, total, status}) - poll getExtractRulesProgress for a
// per-clause progress bar instead of waiting on one big request.
export function startExtractRules(documentId: string, { provider, domain, knownIntents, knownFields, knownRoles }: any = {}) {
  return fetch(`/design/skills/documents/${documentId}/extract-rules/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      provider: provider || null,
      domain: domain || null,
      known_intents: knownIntents || [],
      known_fields: knownFields || [],
      known_roles: knownRoles || [],
    }),
  }).then(jsonOrThrow);
}

// {document_id, completed, total, status: "running"|"done"|"error", result, error}
export function getExtractRulesProgress(documentId: string) {
  return fetch(`/design/skills/documents/${documentId}/extract-rules/progress`).then(jsonOrThrow);
}

export function listAllRules() {
  return fetch("/design/skills/candidate-rules").then(jsonOrThrow);
}

export function listRules(documentId: string) {
  return fetch(
    `/design/skills/candidate-rules?document_id=${encodeURIComponent(documentId)}`
  ).then(jsonOrThrow);
}

export function approveRule(candidateRuleId: string, { version = "1.0" } = {}) {
  return fetch(
    `/design/skills/candidate-rules/${candidateRuleId}/approve`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ approved_by: "ui_reviewer", version }),
    }
  ).then(jsonOrThrow);
}

export function rejectRule(candidateRuleId: string, reason: string) {
  return fetch(
    `/design/skills/candidate-rules/${candidateRuleId}/reject`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ rejected_by: "ui_reviewer", reason }),
    }
  ).then(jsonOrThrow);
}

export function approveAllRules(documentId: string, { version = "1.0" } = {}) {
  return fetch(`/design/skills/documents/${documentId}/approve-all`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ approved_by: "ui_reviewer", version }),
  }).then(jsonOrThrow);
}

export function resetApprovals(documentId: string) {
  return fetch(`/design/skills/documents/${documentId}/reset-approvals`, {
    method: "POST",
  }).then(jsonOrThrow);
}

export function publishSkill(skillId: string, { documentId, name, domain }: any) {
  return fetch(`/design/skills/${skillId}/publish`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ document_id: documentId, name, domain }),
  }).then(jsonOrThrow);
}

export function editRule(candidateRuleId: string, rule: any) {
  return fetch(`/design/skills/candidate-rules/${candidateRuleId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ rule }),
  }).then(jsonOrThrow);
}

export function listDomainPacks() {
  return fetch("/design/skills/domain-packs").then(jsonOrThrow);
}

export function profileDocument(documentId: string, { pack, provider }: any = {}) {
  return fetch(`/design/skills/documents/${documentId}/profile`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ pack: pack || null, provider: provider || null }),
  }).then(jsonOrThrow);
}

export function classifyClauses(documentId: string, { provider }: any = {}) {
  return fetch(`/design/skills/documents/${documentId}/classify-clauses`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ provider: provider || null }),
  }).then(jsonOrThrow);
}

export function extractAtoms(documentId: string, { provider }: any = {}) {
  return fetch(`/design/skills/documents/${documentId}/extract-policy-atoms`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ provider: provider || null }),
  }).then(jsonOrThrow);
}

export function validateDocument(documentId: string, { pack, declaredParams, metrics }: any = {}) {
  return fetch(`/design/skills/documents/${documentId}/validate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      pack: pack || null,
      declared_params: declaredParams || [],
      metrics: metrics || [],
    }),
  }).then(jsonOrThrow);
}

export function listUnresolved(documentId: string) {
  return fetch(`/design/skills/documents/${documentId}/unresolved-items`).then(jsonOrThrow);
}

export function resolveUnresolved(unresolvedId: string, { status = "resolved", notes }: any = {}) {
  return fetch(`/design/skills/unresolved-items/${encodeURIComponent(unresolvedId)}/resolve`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ status, resolved_by: "ui_reviewer", notes: notes || null }),
  }).then(jsonOrThrow);
}

export function getClauseLedger(documentId: string) {
  return fetch(`/design/skills/documents/${documentId}/clause-ledger`).then(jsonOrThrow);
}

export function getProfile(documentId: string) {
  return fetch(`/design/skills/documents/${documentId}/profile`).then(jsonOrThrow);
}

export function listAtoms(documentId: string) {
  return fetch(`/design/skills/documents/${documentId}/policy-atoms`).then(jsonOrThrow);
}

/** Fetch the persisted audit log for a document from our API server */
export function fetchAuditLog(documentId: string) {
  return fetch(`/api/audit?documentId=${encodeURIComponent(documentId)}`).then(jsonOrThrow);
}

export function parseSchema(ddl: string, datasourceId: string) {
  return fetch("/design/semantic/catalog/parse", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ddl, datasource_id: datasourceId }),
  }).then(jsonOrThrow);
}

/** Forget all connected datasources server-side: clears the semantic-layer
 *  datasource/function/query-template store and removes published artifact dirs
 *  (the securebank-demo baseline is kept). */
export function resetDatasources() {
  return fetch("/design/semantic/reset", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ keep_baselines: true }),
  }).then(jsonOrThrow);
}

export function introspect(dsn: string, { datasourceId, schema }: any = {}) {
  return fetch("/design/semantic/catalog/introspect", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ dsn, datasource_id: datasourceId, schema }),
  }).then(jsonOrThrow);
}

/** Learn a generic MCP server's tools and represent them as a catalog — one
 *  "table" per tool, one "column" per input-schema property — the MCP analog
 *  of introspect(). Persists the source so build/import/publish-policy can
 *  rebuild it later from just datasourceId, the same way a dsn-based
 *  datasource already can. */
export function introspectMcp(serverUrl: string, headers: Record<string, string>, datasourceId?: string) {
  return fetch("/design/semantic/mcp/introspect", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ server_url: serverUrl, headers: headers || {}, datasource_id: datasourceId }),
  }).then(jsonOrThrow);
}

export function buildInterfaces({ rules, ddl, dsn, domain, datasourceId, intents, metrics, callerContext, modelId }: any) {
  return fetch("/design/semantic/build", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      rules, ddl, dsn, domain, datasource_id: datasourceId, intents,
      metrics: metrics || {}, caller_context: callerContext || {},
      model_id: modelId || "semantic_model",
    }),
  }).then(jsonOrThrow);
}

export function importDbtModel({ dbtModel, overlay, ddl, dsn, domain, modelId, datasourceId }: any) {
  return fetch("/design/semantic/import/dbt", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      dbt_model: dbtModel, overlay, ddl, dsn, domain,
      model_id: modelId || "semantic_model", datasource_id: datasourceId,
    }),
  }).then(jsonOrThrow);
}

export function listTemplates(semanticModelId?: string) {
  const q = semanticModelId ? `?semantic_model_id=${encodeURIComponent(semanticModelId)}` : "";
  return fetch(`/design/semantic/templates${q}`).then(jsonOrThrow);
}

/** Guess which schema fields are PII (Presidio, name-based). Send the column
 *  list; get back a best-guess entity + label + score per detected column. */
export function analyzePii(fields: { table?: string; column: string; type?: string }[]) {
  return fetch("/pii/analyze", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ fields }),
  }).then(jsonOrThrow);
}

/** Fetch the published, bound policy bundle for a datasource (authoritative
 *  rule->column bindings). Returns { policy_bundle: {} } if nothing published. */
export function getPolicy(datasourceId?: string) {
  const q = datasourceId ? `?datasource_id=${encodeURIComponent(datasourceId)}` : "";
  return fetch(`/design/semantic/policy${q}`).then(jsonOrThrow);
}

export function approveTemplate(templateId: string) {
  return fetch(`/design/semantic/templates/${templateId}/approve`, { method: "POST" }).then(jsonOrThrow);
}

export function rejectTemplate(templateId: string) {
  return fetch(`/design/semantic/templates/${templateId}/reject`, { method: "POST" }).then(jsonOrThrow);
}

export function publishTemplates(semanticModelId?: string) {
  return fetch("/design/semantic/publish", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ semantic_model_id: semanticModelId || null }),
  }).then(jsonOrThrow);
}

export function publishPolicy({ rules, ddl, dsn, domain, datasourceId, metrics }: any) {
  return fetch("/design/semantic/publish-policy", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      rules, ddl, dsn, domain, datasource_id: datasourceId, metrics: metrics || {},
    }),
  }).then(jsonOrThrow);
}

// ── Settings: finding-severity mapping (api-server, DB-backed per demo) ──

import type { SeverityRule } from "./severity";

/** The demo's effective ordered rule-list (stored, or the built-in defaults). */
export function getSeverityRules(demo: string): Promise<{ demo: string; rules: SeverityRule[]; isDefault: boolean }> {
  return fetch(`/api/settings/severity?demo=${encodeURIComponent(demo)}`).then(jsonOrThrow);
}

/** Replace the demo's whole ordered rule-list. */
export function saveSeverityRules(demo: string, rules: SeverityRule[]) {
  return fetch("/api/settings/severity", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ demo, rules }),
  }).then(jsonOrThrow);
}

/** Drop the demo's stored rules so the defaults apply again. */
export function resetSeverityRules(demo: string) {
  return fetch(`/api/settings/severity?demo=${encodeURIComponent(demo)}`, { method: "DELETE" }).then(jsonOrThrow);
}

// Compliance (compliance_design.md, Layer B): draft a CANDIDATE overlay from
// the PII scan's per-column entities. semantic-layer never writes it anywhere.
export function suggestComplianceOverlay(body: {
  deployment: string; policy_document: string;
  fields: { table: string; column: string; entity: string }[]; frameworks: string[];
}): Promise<{ overlay: any; yaml: string; unmapped: { column: string; entity: string }[]; bound: number }> {
  return fetch("/design/semantic/compliance/overlay/suggest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).then(jsonOrThrow);
}

// ── Check enablement (eval-engine, /eval/checks) ─────────────────────────────
// Which of the engine's checks this deployment runs. NOT demo-scoped, unlike
// the severity mapping above: `/eval/*` is the engine's own surface and knows
// nothing about demos (see prefront-ui/CLAUDE.md's caveat on the same point
// for `/oob/*`), so this is one setting for the deployment, and the panel
// says so rather than implying a per-demo one.

export interface CheckInfo {
  check_id: string;
  title: string;
  detail: string;
  /** Runs on demand over many sessions (POST /eval/population), not per session. */
  population: boolean;
  enabled: boolean;
}
export interface CheckFamily {
  family: string;
  label: string;
  /** False when the family's artifact is missing — a rule pack for Policy, an
   *  intent catalog for Conformance. Those checks are idle whatever the
   *  toggle says, and the panel has to show the difference. */
  configured: boolean;
  checks: CheckInfo[];
}
export interface ChecksResponse {
  families: CheckFamily[];
  disabled: string[];
  /** Short hash of the disabled set; part of the engine's evaluation version key. */
  version: string;
  total: number;
  enabled: number;
  /** Only on PUT: ids the engine did not recognise and therefore dropped. */
  unknown?: string[];
}

export function getChecks(): Promise<ChecksResponse> {
  return fetch("/eval/checks").then(jsonOrThrow);
}

/** Replace the whole disabled set (the engine takes the list, not a diff). */
export function saveChecks(disabled: string[]): Promise<ChecksResponse> {
  return fetch("/eval/checks", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ disabled }),
  }).then(jsonOrThrow);
}

/** Forget the stored set — every check enabled again. */
export function resetChecks(): Promise<ChecksResponse> {
  return fetch("/eval/checks", { method: "DELETE" }).then(jsonOrThrow);
}
