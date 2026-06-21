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

export function introspect(dsn: string, { datasourceId, schema }: any = {}) {
  return fetch("/design/semantic/catalog/introspect", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ dsn, datasource_id: datasourceId, schema }),
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
