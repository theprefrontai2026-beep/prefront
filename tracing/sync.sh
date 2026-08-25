#!/usr/bin/env sh
# Copy the canonical tracing module into every service package.
#
# Each service has its own Docker build context, so they cannot import a shared
# module — the file is vendored instead. Edit tracing/prefront_tracing.py and
# run this; CI-free drift check: `sh tracing/sync.sh --check`.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
src="$root/tracing/prefront_tracing.py"

targets="
skill-builder/skillbuilder/prefront_tracing.py
semantic-layer/semanticlayer/prefront_tracing.py
semantic-mcp-server/semanticmcp/prefront_tracing.py
securebank-demo/prefront_tracing.py
loanpro-demo/prefront_tracing.py
"

status=0
for rel in $targets; do
  dst="$root/$rel"
  if [ "${1:-}" = "--check" ]; then
    if ! cmp -s "$src" "$dst"; then
      echo "DRIFT: $rel differs from tracing/prefront_tracing.py" >&2
      status=1
    fi
  else
    cp "$src" "$dst"
    echo "synced $rel"
  fi
done
exit $status
