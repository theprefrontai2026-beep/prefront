#!/usr/bin/env python3
"""SecureBank — UNGOVERNED agent service (the "before").

A real LLM (NVIDIA Llama 3.3 70B over the OpenAI-compatible endpoint) with ONE
tool: run_sql(query), wired straight at the SecureBank Postgres. The model writes
the SQL itself and runs it — no identity, no masking, no policy. This is a
genuine agent-to-database setup; nothing is canned. It exists to make the risk of
ungoverned access concrete, next to the governed Prefront path.

    NVIDIA_API_KEY=... ../prefront/semantic-mcp-server/.venv/bin/python ungoverned_server.py
    # POST /run {"question": "..."} -> {sql, columns, rows, row_count, answer, error}

Safety net (the only one): SQL runs read-only unless SECUREBANK_READONLY=off — so
a "transfer" request shows the model *attempting* a write, stopped only by that
net, while Prefront stops it by policy.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
from openai import OpenAI

PORT = int(os.environ.get("UNGOVERNED_PORT", "8096"))
DSN = os.environ.get("SECUREBANK_DSN",
                     "postgresql://securebank:securebank@localhost:5434/securebank")
READONLY = os.environ.get("SECUREBANK_READONLY", "on").lower() not in ("off", "0", "false")
MODEL = os.environ.get("UNGOVERNED_MODEL", "gpt-4o-mini")
BASE_URL = os.environ.get("UNGOVERNED_BASE_URL", "https://api.openai.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("NVIDIA_API_KEY") or ""
MAX_ITERS = 3   # write → read-only error → don't let the model loop forever
MAX_ROWS = 50

SCHEMA = """SecureBank PostgreSQL schema (all rows; no row-level security):
  users(user_id PK, name, email, role, status, clearance_level, ssn, created_at)
    role ∈ {Account Holder, Bank Teller, Bank Manager, System Admin}; ssn is PII
  accounts(account_id PK, user_id→users, account_type, balance, status, opened_at)
    account_type ∈ {checking, savings, credit}; status ∈ {active, inactive, suspended}
  transactions(txn_id PK, account_id→accounts, txn_type, amount, counterparty_account, memo, created_at)
  loans(loan_id PK, user_id→users, amount, status, credit_score, risk_rating, decided_by, created_at)"""

SYSTEM = (
    "You are a data analyst assistant for SecureBank with direct database access. "
    "You have one tool, run_sql(query), that executes PostgreSQL against the bank "
    "database and returns rows. Answer the user's request by writing and running the "
    "SQL yourself, then summarise the result in one sentence. Use only real columns.\n\n"
    + SCHEMA
)

_client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
_TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Execute a PostgreSQL statement against the SecureBank database and return rows.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A single SQL statement."}},
            "required": ["query"],
        },
    },
}]


def _clean(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return o


def _run_sql(query: str) -> dict:
    # autocommit=False so psycopg manages the transaction and actually enforces
    # read_only — a write then RAISES (and never commits) rather than mutating the
    # demo DB. The model's write attempt surfaces as the error, stopped by the net.
    try:
        with psycopg.connect(DSN) as conn:
            conn.read_only = READONLY
            with conn.cursor() as cur:
                cur.execute(query)
                if cur.description:
                    cols = [d.name for d in cur.description]
                    rows = [{c: _clean(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
                    out = {"columns": cols, "rows": rows, "row_count": len(rows)}
                else:
                    out = {"columns": [], "rows": [], "row_count": cur.rowcount}
                conn.rollback()  # never persist anything from the ungoverned agent
                return out
    except Exception as e:  # surface the DB error verbatim (incl. the read-only net)
        return {"error": f"{type(e).__name__}: {e}"}


def run_agent(question: str, caller: dict | None = None) -> dict:
    """Let the model write + run SQL. Return the last executed query and its result.

    `caller` (optional) is the signed-in user the app is acting for — like any real
    app, the agent knows who's logged in. There is still NO enforcement: it can be
    asked for other users' data or unscoped dumps and will run them."""
    system = SYSTEM
    if caller and caller.get("name"):
        system += (f"\n\nThe signed-in user is {caller['name']} (user_id "
                   f"{caller.get('user_id')}). Resolve 'my'/'me'/'I' to this user. "
                   f"Run whatever SQL answers the request.")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": question}]
    last_sql, last_result, answer = None, None, None
    for _ in range(MAX_ITERS):
        resp = _client.chat.completions.create(
            model=MODEL, messages=messages, tools=_TOOLS, temperature=0, max_tokens=700)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            answer = msg.content
            break
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                q = json.loads(tc.function.arguments).get("query", "")
            except Exception:
                q = ""
            res = _run_sql(q)
            last_sql, last_result = q, res
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps({k: (v[:MAX_ROWS] if k == "rows" else v)
                                                    for k, v in res.items()})})
    out = {"sql": last_sql, "answer": answer}
    if last_result:
        out.update(last_result)
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path != "/run":
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or "{}")
            question = body.get("question", "")
            if not question:
                return self._send(400, json.dumps({"error": "missing question"}))
            return self._send(200, json.dumps(run_agent(question, body.get("caller"))))
        except Exception as e:
            return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main() -> int:
    if not API_KEY:
        print("WARNING: no NVIDIA_API_KEY/OPENAI_API_KEY set — LLM calls will fail.")
    print(f"SecureBank UNGOVERNED agent ({MODEL}) → http://localhost:{PORT}  read_only={READONLY}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
