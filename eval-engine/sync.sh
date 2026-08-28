#!/usr/bin/env sh
# Copy the subset of eval-engine that semantic-mcp-server's inline reuse
# (autonomous_build.md step 18) needs into that service's package.
#
# Each service has its own Docker build context, so they cannot import a
# shared module - the files are vendored instead, same pattern as
# tracing/sync.sh. Only the dependency-light modules are copied: contract.py
# (the Verdict/Session/Step dataclasses), provenance.py (build()/flatten()),
# combinator.py, family1/, family2/, family3/ (all import only stdlib +
# pyyaml + these - never fastapi/clickhouse-connect, which stay
# eval-engine-only). config.py, binding.py, visibility.py, reconstruct.py,
# ch.py, store.py, api.py, worker.py, evaluate.py, profiles/ are NOT
# vendored - semantic-mcp-server's governance/session_state.py +
# inline_checks.py build the Session directly from accumulated per-connection
# history instead (see inline_checks.py's docstring).
#
# Edit eval-engine/evalengine/{contract,provenance,combinator}.py or
# family1//family3/ and run this; drift check: `sh eval-engine/sync.sh --check`.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
src_root="$root/eval-engine/evalengine"
dst_root="$root/semantic-mcp-server/semanticmcp/evalengine"

check=0
[ "${1:-}" = "--check" ] && check=1
status=0

sync_file() {
  rel_path="$1"
  src_file="$src_root/$rel_path"
  dst_file="$dst_root/$rel_path"
  if [ "$check" = 1 ]; then
    if [ ! -f "$dst_file" ] || ! cmp -s "$src_file" "$dst_file"; then
      echo "DRIFT: semanticmcp/evalengine/$rel_path differs from evalengine/$rel_path" >&2
      status=1
    fi
  else
    mkdir -p "$(dirname "$dst_file")"
    cp "$src_file" "$dst_file"
    echo "synced semanticmcp/evalengine/$rel_path"
  fi
}

for top_file in __init__.py contract.py provenance.py combinator.py visibility.py; do
  sync_file "$top_file"
done
for sub_dir in family1 family2 family3; do
  for src_path in "$src_root/$sub_dir"/*.py; do
    sync_file "$sub_dir/$(basename "$src_path")"
  done
done
exit $status
