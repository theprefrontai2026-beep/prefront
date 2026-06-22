#!/usr/bin/env bash
# Drive the Skill Builder design-time pipeline over one policy document, end to
# end, with curl. JSON is parsed with python3 (no jq dependency).
#
# Usage:
#   scripts/run_policy.sh <document> [domain] [skill_id] [version] [ddl_file]
#
# Example:
#   scripts/run_policy.sh \
#     ../../commercerisk-demo/business-docs/CR-FIN-001-credit-and-collections-policy.md \
#     credit_collections cr_fin_001 3.2 ../../commercerisk-demo/db/schema.sql
#
# The optional <ddl_file> is the datasource schema (.sql). When given, it is
# attached at upload so the Skill Builder derives a column-only default domain
# pack from it — grounding extraction/validation in vocabulary the binder can
# actually resolve. Defaults to $SCHEMA (the bundled CommerceRisk schema).
#
# Env overrides:
#   SB      skill-builder base URL   (default http://localhost:8000)
#   SL      semantic-layer base URL  (default http://localhost:8010)
#   SCHEMA  datasource schema .sql for grounding + the binder round-trip
#           (default ../../commercerisk-demo/db/schema.sql; steps skipped if missing)
#   APPROVE if "0", do not auto-approve / publish / bind (inspect only)
set -euo pipefail

DOC="${1:?usage: run_policy.sh <document> [domain] [skill_id] [version] [ddl_file]}"
DOMAIN="${2:-credit_collections}"
SKILL_ID="${3:-cr_fin_001}"
VERSION="${4:-1.0}"

SB="${SB:-http://localhost:8000}"
SL="${SL:-http://localhost:8010}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# default: the bundled CommerceRisk schema, two repos over from skill-builder/
SCHEMA="${SCHEMA:-$SCRIPT_DIR/../../../commercerisk-demo/db/schema.sql}"
# 5th positional arg overrides the DDL used for grounding at upload; falls back
# to $SCHEMA so the binder round-trip and grounding share one schema by default.
DDL_FILE="${5:-$SCHEMA}"
APPROVE="${APPROVE:-1}"

# Step 7 (binder round-trip) publishes to the bundled semantic-layer at $SL,
# which writes ONE deployment's bundle (the bundled CommerceRisk one:
# /artifacts/commercerisk/policy.yaml). Running it for an unrelated domain would
# overwrite that bundle. So it's gated:
#   BIND=auto (default) -> run only when $DOMAIN is in $BIND_DOMAINS
#   BIND=1              -> always run (you accept it writes the $SL deployment)
#   BIND=0              -> never run
# DATASOURCE_ID / METRICS are no longer hardcoded — override per datasource.
BIND="${BIND:-auto}"
BIND_DOMAINS="${BIND_DOMAINS:-credit_collections commercerisk}"
DATASOURCE_ID="${DATASOURCE_ID:-commercerisk_postgres}"
METRICS="${METRICS:-{\"available_credit\":\"credit_limit - current_balance\"}}"

[ -f "$DOC" ] || { echo "document not found: $DOC" >&2; exit 1; }

# Pull a top-level string field out of a JSON stdin.
jget() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }
pp()   { python3 -m json.tool; }
hr()   { printf '\n=== %s ===\n' "$1"; }

hr "1. health"
curl -fsS "$SB/healthz"; echo

hr "2. upload  ($DOC)"
UPLOAD_ARGS=(-F "file=@$DOC" -F "domain=$DOMAIN" -F "version=$VERSION")
if [ -n "$DDL_FILE" ] && [ -f "$DDL_FILE" ]; then
  echo "attaching datasource schema for grounding: $DDL_FILE ($(wc -c <"$DDL_FILE") bytes)"
  UPLOAD_ARGS+=(-F "ddl_file=@$DDL_FILE" -F "datasource_id=$(basename "$DDL_FILE")")
else
  echo "no datasource schema attached (grounding by named pack only); DDL_FILE='$DDL_FILE'"
fi
DID=$(curl -fsS "${UPLOAD_ARGS[@]}" "$SB/design/skills/documents/upload" | jget document_id)
[ -n "$DID" ] || { echo "upload failed (no document_id)" >&2; exit 1; }
echo "document_id=$DID"

hr "3. run-full-extraction (profile -> classify -> atoms -> rules -> validate)"
curl -fsS -X POST "$SB/design/skills/documents/$DID/run-full-extraction" \
  -H 'content-type: application/json' \
  -d "{\"pack\":\"$DOMAIN\",\"skill_id\":\"$SKILL_ID\"}" | pp

hr "4. validation report"
curl -fsS "$SB/design/skills/documents/$DID/validation-report" | pp

hr "4b. unresolved items"
curl -fsS "$SB/design/skills/documents/$DID/unresolved-items" | pp

hr "4c. clause ledger"
curl -fsS "$SB/design/skills/documents/$DID/clause-ledger" | pp

if [ "$APPROVE" = "0" ]; then
  echo; echo "APPROVE=0 -> stopping after inspection. document_id=$DID"
  exit 0
fi

hr "5. approve every candidate rule"
for CRID in $(curl -fsS "$SB/design/skills/candidate-rules?document_id=$DID" \
              | grep -oE '"candidate_rule_id":"[^"]+"' | cut -d'"' -f4); do
  curl -fsS -X POST "$SB/design/skills/candidate-rules/$CRID/approve" \
    -H 'content-type: application/json' \
    -d "{\"approved_by\":\"run_policy.sh\",\"version\":\"$VERSION\"}" >/dev/null
  echo "approved $CRID"
done

hr "6. publish skill ($SKILL_ID)"
curl -fsS -X POST "$SB/design/skills/$SKILL_ID/publish" \
  -H 'content-type: application/json' \
  -d "{\"document_id\":\"$DID\",\"name\":\"$SKILL_ID\",\"domain\":\"$DOMAIN\",\"approved_only\":true}" | pp

hr "7. binder round-trip (semantic-layer publish-policy)"
# Decide whether to bind, without clobbering the wrong deployment's bundle.
run_bind=0
case "$BIND" in
  1) run_bind=1 ;;
  0) run_bind=0 ;;
  auto)
    for d in $BIND_DOMAINS; do [ "$DOMAIN" = "$d" ] && run_bind=1; done ;;
esac

if [ "$run_bind" != 1 ]; then
  echo "skipping binder round-trip: domain '$DOMAIN' is not in BIND_DOMAINS ($BIND_DOMAINS)."
  echo "  The bundled semantic-layer at $SL writes ONE deployment's bundle"
  echo "  (/artifacts/<deployment>/policy.yaml); binding a different domain there"
  echo "  would overwrite it. To bind anyway, set the target explicitly:"
  echo "    BIND=1 DATASOURCE_ID=<id> METRICS='<json>' SL=<url> $0 ..."
elif [ ! -f "$SCHEMA" ]; then
  echo "schema not found at $SCHEMA — skipping binder round-trip."
  echo "set SCHEMA=/path/to/schema.sql (or pass the ddl_file arg) to enable it."
else
  SB="$SB" SL="$SL" DID="$DID" DOMAIN="$DOMAIN" SCHEMA="$SCHEMA" \
  DATASOURCE_ID="$DATASOURCE_ID" METRICS="$METRICS" python3 - <<'PY'
import os, json, urllib.request
SB, SL, DID, DOMAIN, SCHEMA = (os.environ[k] for k in ("SB","SL","DID","DOMAIN","SCHEMA"))
DATASOURCE_ID = os.environ["DATASOURCE_ID"]
try:
    METRICS = json.loads(os.environ.get("METRICS") or "{}")
except json.JSONDecodeError as e:
    raise SystemExit(f"METRICS is not valid JSON: {e}")
def get(u): return json.load(urllib.request.urlopen(u))
def post(u,b):
    r=urllib.request.Request(u, json.dumps(b).encode(), {'content-type':'application/json'})
    return json.load(urllib.request.urlopen(r))
rows=get(f"{SB}/design/skills/candidate-rules?document_id={DID}")["candidate_rules"]
approved=[r["rule"] for r in rows if r["review_status"]=="approved"]
print(f"binding {len(approved)} approved rule(s) -> datasource_id={DATASOURCE_ID} metrics={list(METRICS)}")
res=post(f"{SL}/design/semantic/publish-policy", {
    "rules":approved, "ddl":open(SCHEMA).read(), "domain":DOMAIN,
    "datasource_id":DATASOURCE_ID,
    "metrics":METRICS,
})
print("published:", res["published"], res["rules"])
for r in res.get("rejected", []):
    print("rejected :", r["rule_key"], "->", r["reasons"][0])
PY
fi

echo; echo "done. document_id=$DID"
