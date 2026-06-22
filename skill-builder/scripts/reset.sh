#!/usr/bin/env bash
# Wipe the Prefront DESIGN-TIME state to a clean slate:
#   • Skill Builder — empty every data table (keeping the schema + alembic state)
#     and delete generated artifact files (/data/skills/*).
#   • Semantic layer — clear the functions / datasources / query_templates store
#     and remove published per-datasource artifacts (/artifacts/<id>/), KEEPING
#     the demo baseline (securebank-demo) unless WIPE_ALL=1.
# The datasource databases (the SecureBank Postgres) are NOT touched.
#
# NOTE: the UI's "connected datasource" is browser-only state (localStorage keys
# prefront.schema / prefront.intents) — there is no server record to clear, so
# this script cannot reset it. The closing note prints the console one-liner to
# wipe it in the browser.
#
# Usage:
#   scripts/reset.sh            # shows what will be wiped, then prompts
#   scripts/reset.sh -y         # skip the prompt (also: FORCE=1 scripts/reset.sh)
#
# Env overrides:
#   COMPOSE_DIR  dir containing docker-compose.yaml (default: repo root, ../..)
#   SB_SVC       skill-builder service name (default: skill-builder)
#   DB_SVC       design-time Postgres service name (default: skill-builder-db)
#   DB_USER/DB_NAME  Postgres creds (default: skillbuilder/skillbuilder)
#   SL_SVC       semantic-layer-api service name (default: semantic-layer-api)
#   KEEP_ARTIFACTS  artifact dirs to preserve (default: "securebank-demo")
#   WIPE_ALL=1   also remove the kept baselines (a full /artifacts wipe)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_DIR="${COMPOSE_DIR:-$SCRIPT_DIR/../..}"
SB_SVC="${SB_SVC:-skill-builder}"
DB_SVC="${DB_SVC:-skill-builder-db}"
DB_USER="${DB_USER:-skillbuilder}"
DB_NAME="${DB_NAME:-skillbuilder}"
SL_SVC="${SL_SVC:-semantic-layer-api}"
KEEP_ARTIFACTS="${KEEP_ARTIFACTS:-securebank-demo}"
[ "${WIPE_ALL:-0}" = "1" ] && KEEP_ARTIFACTS=""

# Python that clears the semantic-layer SQLite store (keeps the table schema).
read -r -d '' SL_CLEAR <<'PY' || true
import sqlite3, os
p = os.environ.get("SEMANTICLAYER_DB", "semanticlayer.db")
c = sqlite3.connect(p)
have = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
for t in ("functions", "datasources", "query_templates"):
    if t in have:
        c.execute("DELETE FROM " + t)
c.commit()
print("cleared:", sorted(have & {"functions", "datasources", "query_templates"}))
PY

FORCE="${FORCE:-0}"
case "${1:-}" in
  -y|--yes|--force) FORCE=1 ;;
esac

cd "$COMPOSE_DIR"
# All docker/psql calls read from /dev/null so 'docker compose exec -T' never
# drains the interactive confirmation prompt's stdin.
dc() { docker compose "$@" </dev/null; }
psql_q() { dc exec -T "$DB_SVC" psql -U "$DB_USER" -d "$DB_NAME" -tA -c "$1"; }

# Fail early with a clear message if the containers aren't up.
if ! dc exec -T "$DB_SVC" true >/dev/null 2>&1; then
  echo "error: '$DB_SVC' is not running. Start it with: docker compose up -d $SB_SVC" >&2
  exit 1
fi

echo "=== Skill Builder reset — current contents (in $COMPOSE_DIR) ==="
psql_q "
  SELECT 'source_documents      = '||count(*) FROM source_documents
  UNION ALL SELECT 'candidate_rules       = '||count(*) FROM candidate_rules
  UNION ALL SELECT 'approved_policy_rules = '||count(*) FROM approved_policy_rules
  UNION ALL SELECT 'unresolved_items      = '||count(*) FROM unresolved_items
  UNION ALL SELECT 'skill_versions        = '||count(*) FROM skill_versions;" || true
echo "artifact skill dirs   = $(dc exec -T "$SB_SVC" sh -c 'ls -1 /data/skills 2>/dev/null | wc -l' | tr -d '[:space:]')"

# Semantic layer is optional — only report/clear it if the service is up.
SL_UP=0
if dc exec -T "$SL_SVC" true >/dev/null 2>&1; then SL_UP=1; fi
if [ "$SL_UP" = "1" ]; then
  echo "--- semantic layer ---"
  dc exec -T "$SL_SVC" python -c "import sqlite3,os;p=os.environ.get('SEMANTICLAYER_DB','semanticlayer.db');c=sqlite3.connect(p);have={r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")};print('\n'.join('%-22s= %d'%(t,c.execute('select count(*) from '+t).fetchone()[0]) for t in ('functions','datasources','query_templates') if t in have))" 2>/dev/null || echo "(store empty / fresh)"
  echo "published artifact dirs = $(dc exec -T "$SL_SVC" sh -c 'ls -1d /artifacts/*/ 2>/dev/null | wc -l' | tr -d '[:space:]') (keeping: ${KEEP_ARTIFACTS:-<none>})"
else
  echo "--- semantic layer: '$SL_SVC' not running, skipping ---"
fi

if [ "$FORCE" != "1" ]; then
  echo
  echo "This permanently deletes ALL uploaded docs, generated/approved rules, and"
  echo "artifact files for Skill Builder, AND the semantic-layer functions/datasources/"
  echo "templates store + published artifacts (keeping: ${KEEP_ARTIFACTS:-<none>}). Schemas preserved."
  printf "Type 'yes' to proceed: "
  read -r ans || ans=""   # EOF (e.g. piped input) -> treat as no, don't trip set -e
  [ "$ans" = "yes" ] || { echo "aborted."; exit 0; }
fi

echo "=== truncating all data tables (alembic_version kept) ==="
psql_q "
DO \$\$ DECLARE r RECORD; BEGIN
  FOR r IN SELECT tablename FROM pg_tables
           WHERE schemaname='public' AND tablename <> 'alembic_version' LOOP
    EXECUTE 'TRUNCATE TABLE '||quote_ident(r.tablename)||' RESTART IDENTITY CASCADE';
  END LOOP;
END \$\$;"

echo "=== clearing generated artifact files (/data/skills/*) ==="
dc exec -T -u 0 "$SB_SVC" sh -c 'rm -rf /data/skills/* 2>/dev/null; echo cleared'

if [ "$SL_UP" = "1" ]; then
  echo "=== clearing semantic-layer store (functions/datasources/query_templates) ==="
  dc exec -T "$SL_SVC" python -c "$SL_CLEAR"
  echo "=== removing published artifacts (/artifacts/*), keeping: ${KEEP_ARTIFACTS:-<none>} ==="
  dc exec -T -u 0 -e KEEP="$KEEP_ARTIFACTS" "$SL_SVC" sh -c '
    for d in /artifacts/*/; do
      [ -d "$d" ] || continue
      n=$(basename "$d"); skip=0
      for k in $KEEP; do [ "$n" = "$k" ] && skip=1; done
      [ "$skip" = 1 ] || rm -rf "$d"
    done
    echo "remaining: $(ls -1 /artifacts 2>/dev/null | tr "\n" " ")"'
fi

echo "=== verify ==="
echo "documents = $(psql_q 'SELECT count(*) FROM source_documents' | tr -d '[:space:]'); \
candidate_rules = $(psql_q 'SELECT count(*) FROM candidate_rules' | tr -d '[:space:]'); \
artifact dirs = $(dc exec -T "$SB_SVC" sh -c 'ls -1 /data/skills 2>/dev/null | wc -l' | tr -d '[:space:]')"
if [ "$SL_UP" = "1" ]; then
  echo "semantic store = $(dc exec -T "$SL_SVC" python -c "import sqlite3,os;p=os.environ.get('SEMANTICLAYER_DB','semanticlayer.db');c=sqlite3.connect(p);print(sum(c.execute('select count(*) from '+t).fetchone()[0] for t in ('functions','datasources','query_templates') if c.execute(\"select 1 from sqlite_master where type='table' and name='\"+t+\"'\").fetchone()))" 2>/dev/null) rows; \
artifact dirs = $(dc exec -T "$SL_SVC" sh -c 'ls -1d /artifacts/*/ 2>/dev/null | wc -l' | tr -d '[:space:]')"
fi
echo "schema = $(psql_q 'SELECT version_num FROM alembic_version' | tr -d '[:space:]') (intact)"
echo "done — clean slate."
if [ "$SL_UP" = "1" ] && [ "${WIPE_ALL:-0}" = "1" ]; then
  echo "note: securebank artifacts were removed (WIPE_ALL=1) — re-seed with:"
  echo "      (cd ../securebank-demo && docker compose up -d seed-artifacts mcp)"
fi
echo
echo "note: the connected-datasource state is NOT stored server-side — the UI keeps"
echo "      it only in the browser's localStorage, so this script cannot clear it."
echo "      To wipe it, open the UI's browser devtools Console and run:"
echo
echo "          localStorage.removeItem('prefront.schema');"
echo "          localStorage.removeItem('prefront.intents');"
echo "          location.reload();"
echo
echo "      (or just reconnect a datasource to overwrite it)."
