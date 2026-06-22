# Prefront Semantic MCP Server (POC)

A thin **runtime** that exposes the design-time `query_templates.yaml` (produced by
`semantic-layer/`) over MCP. Each template becomes one MCP tool; calling the tool
runs the template's parameterized SQL against the CommerceRisk Postgres and returns
the rows. The agent only ever sees the available queries as typed tools — the server
is a function wrapper around each approved query.

**POC scope:** no policy, caller context, sensitivity, approval, or writes. It reads
`query_templates.yaml` as data and does not import the `semantic-layer` package.

## Setup

```bash
# 1. Start the datasource (Postgres 16 + schema + seed)
cd ../commercerisk-demo && docker compose up -d

# 2. Install deps into this package's venv (uv)
cd ../semantic-mcp-server
VIRTUAL_ENV=.venv uv venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
```

DB connection comes from `--database-url`, else `$DATABASE_URL`, else the demo DSN
`postgresql://commercerisk:commercerisk@localhost:5432/commercerisk`.

## Use

```bash
# Check DB + templates load
python -m semanticmcp doctor

# Run one query directly (no MCP client needed)
python -m semanticmcp call get_customer_credit \
  --args '{"customer_id": 4, "caller_region": "EMEA"}'

# Serve all templates as an MCP server (stdio)
python -m semanticmcp serve \
  --templates ../semantic-layer/out/commercerisk/query_templates.yaml
```

Each tool's inputs are exactly the `:placeholders` its SQL needs (including
`:caller_*`, which in this POC are ordinary arguments). A call returns
`{tool, row_count, rows, sql}`; errors return `{error, tool, sql}`.

## Ways to call this server (4)

The server speaks MCP over **stdio**: a client launches `python -m semanticmcp serve`
as a subprocess and exchanges JSON-RPC over stdin/stdout. You don't run `serve`
yourself for the MCP options below — they spawn it. Postgres must be up first
(`cd ../commercerisk-demo && docker compose up -d`). Global flags
(`--templates`, `--database-url`) go **before** the subcommand.

### 1. Direct CLI — no MCP protocol (fastest for testing)
Bypasses MCP entirely and runs a template against the DB.
```bash
python -m semanticmcp call find_customers --args '{"caller_region":"EMEA"}'
python -m semanticmcp call get_customer_credit --args '{"customer_id":1,"caller_region":"NA"}'
```

### 2. Client script — real MCP stdio round-trip
`scripts/try_mcp.py` spawns the server, lists tools, and calls one.
```bash
python scripts/try_mcp.py                                        # list + demo call
python scripts/try_mcp.py find_customers '{"caller_region":"APAC"}'
python scripts/try_mcp.py get_customer_credit '{"customer_id":1,"caller_region":"NA"}'
```

### 3. MCP Inspector — visual UI (requires Node/`npx`)
```bash
PYTHONPATH=. npx @modelcontextprotocol/inspector \
  .venv/bin/python -m semanticmcp \
  --templates ../semantic-layer/out/commercerisk/query_templates.yaml serve
```
Pick a tool, fill the form, see the rows in the browser.

### 4. As an agent tool — Claude Code / Claude Desktop
Claude Code:
```bash
claude mcp add prefront-sql -- \
  /home/sachi/prefront/prefront/semantic-mcp-server/.venv/bin/python -m semanticmcp \
  --templates /home/sachi/prefront/prefront/semantic-layer/out/commercerisk/query_templates.yaml serve
```
Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "prefront-sql": {
      "command": "/home/sachi/prefront/prefront/semantic-mcp-server/.venv/bin/python",
      "args": ["-m", "semanticmcp",
               "--templates", "/home/sachi/prefront/prefront/semantic-layer/out/commercerisk/query_templates.yaml",
               "serve"],
      "env": { "DATABASE_URL": "postgresql://commercerisk:commercerisk@localhost:5432/commercerisk" }
    }
  }
}
```

Any MCP-compatible client (Cursor, Continue, custom SDK code, etc.) works the same
way — point it at the `serve` command above.

| # | Method | MCP protocol? | Needs | Best for |
|---|--------|---------------|-------|----------|
| 1 | `semanticmcp call` | No (direct DB) | this CLI | quick query testing |
| 2 | `scripts/try_mcp.py` | Yes (stdio) | this repo | testing the MCP path |
| 3 | MCP Inspector | Yes (stdio) | Node/`npx` | visual exploration |
| 4 | Claude Code / Desktop / other clients | Yes (stdio) | an MCP client | real agent use |

## Layout
```
semanticmcp/
  db.py       psycopg connect + run_select (:name -> %(name)s)
  server.py   load_templates() -> MCP tools; call_template() executes the SQL
  cli.py      serve | call | doctor
```
