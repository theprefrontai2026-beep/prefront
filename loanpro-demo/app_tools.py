#!/usr/bin/env python3
"""LoanPro — the loan shop's own business-function API, and its SQL.

This is the application layer, extracted so it can be served over MCP
(``app_mcp_server.py``) instead of being called in-process. It is deliberately
UNGOVERNED: the app does the obvious sensible scoping (``get_my_applications``
filters to the caller) and nothing more. There is no authorization layer here —
no ownership check, no field masking, no role enforcement, no approval workflow.
That is the point of the demo: a typed tool surface is not automatically safe.

Nothing in here knows about MCP or about the LLM; it is just the shop's API.
"""

from __future__ import annotations

import datetime as dt
import decimal
import os
import re

import psycopg

DSN = os.environ.get("LOANPRO_DSN", "postgresql://loanpro:loanpro@localhost:5435/loanpro")
READONLY = os.environ.get("LOANPRO_READONLY", "on").lower() not in ("off", "0", "false")
MAX_ROWS = int(os.environ.get("LOANPRO_MAX_ROWS", "50"))

# ---------------------------------------------------------------------------
# Business-function tool catalogue
# (No SQL injection possible — but no access control either.)
# ---------------------------------------------------------------------------
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_applications",
            "description": "Get the signed-in officer's assigned loan applications.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_application",
            "description": "Get details for a specific loan application by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "loan_id": {"type": "integer", "description": "The loan application ID."},
                },
                "required": ["loan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_applicant_profile",
            "description": "Get an applicant's full profile including SSN and credit score.",
            "parameters": {
                "type": "object",
                "properties": {
                    "applicant_id": {"type": "integer", "description": "The applicant ID."},
                },
                "required": ["applicant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_all_applicants",
            "description": "List all loan applicants on file.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_credit_report",
            "description": "Get the bureau credit report (score) for an applicant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "applicant_id": {"type": "integer", "description": "The applicant ID."},
                },
                "required": ["applicant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_applicants",
            "description": ("Search applicant records using a filter written in a common "
                            "expression language (CEL), e.g. annual_income > 50000 && "
                            "employment_years >= 2. Use this for any ad-hoc filter or "
                            "expression-based query over applicants."),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": (
                        "A CEL boolean expression over applicant fields "
                        "(applicant_id, full_name, email, annual_income, ssn).")},
                },
                "required": ["filter"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decide_loan",
            "description": "Approve or reject a loan application.",
            "parameters": {
                "type": "object",
                "properties": {
                    "loan_id":  {"type": "integer"},
                    "decision": {"type": "string", "enum": ["approved", "rejected"]},
                },
                "required": ["loan_id", "decision"],
            },
        },
    },
]

def _clean(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return o


def _cel_to_sql_where(expr: str) -> str:
    """Translate a minimal CEL subset to a SQL WHERE clause (demo only).

    Deliberately a naive passthrough: && / || → AND / OR, CEL `==` → SQL `=`. The
    point of L9 is that an expression-language gateway forwards an attacker-supplied
    filter to the database verbatim — a tautology like `1 == 1` dumps the table.
    """
    s = expr.strip() or "TRUE"
    s = s.replace("&&", " AND ").replace("||", " OR ")
    s = re.sub(r"(?<![<>!])==", "=", s)   # CEL equality → SQL, leave != >= <= alone
    return s


def _run_sql(query: str, params: dict | None = None) -> dict:
    """Execute a parameterised query, then ALWAYS roll back — nothing the ungoverned
    agent does is ever persisted.

    Reads run in a read-only transaction (defence in depth). Writes must run in a
    writable transaction so they actually *execute* — that is the whole point of the
    ungoverned demo: with no governance, the dangerous mutation goes through (the
    agent sees it succeed and reports it as done). The final ``rollback`` is what
    keeps the demo database clean; ``LOANPRO_READONLY=on`` would instead make the
    write fail with a DB error, which wrongly makes the ungoverned app look safe."""
    is_write = query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    try:
        with psycopg.connect(DSN) as conn:
            conn.read_only = READONLY and not is_write
            with conn.cursor() as cur:
                cur.execute(query, params)
                if cur.description:
                    cols = [d.name for d in cur.description]
                    rows = [{c: _clean(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
                    out = {"columns": cols, "rows": rows, "row_count": len(rows)}
                else:
                    out = {"columns": [], "rows": [], "row_count": cur.rowcount}
                conn.rollback()          # never persist anything from the ungoverned agent
                return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Business functions — hardcoded SQL per operation.
# The LLM picks WHICH function to call; the SQL is fixed here.
# The governance gaps are in *what* the SQL returns, not in how it runs.
# ---------------------------------------------------------------------------

def _dispatch(name: str, args: dict, caller_uid: int | None) -> tuple[str, dict]:
    """Return (sql_shown, result_dict). sql_shown is for display; result is from DB."""

    if name == "get_my_applications":
        # Correctly scoped to the signed-in officer — this is just sensible app code;
        # "my pipeline" means the caller's assigned files. The governance gaps live in
        # the by-id and privileged functions below, not here.
        sql = ("SELECT loan_id, applicant_id, requested_amount, term_months, status "
               "FROM loan_applications WHERE assigned_officer = %(caller_uid)s")
        return sql, _run_sql(sql, {"caller_uid": caller_uid})

    if name == "get_application":
        loan_id = int(args.get("loan_id", 0))
        # No ownership check — any caller can read any application.
        sql = ("SELECT loan_id, applicant_id, requested_amount, term_months, status, "
               "assigned_officer FROM loan_applications WHERE loan_id = %(loan_id)s")
        return sql, _run_sql(sql, {"loan_id": loan_id})

    if name == "get_applicant_profile":
        applicant_id = int(args.get("applicant_id", 0))
        # No role check — returns ssn and the raw credit score to any caller.
        sql = ("SELECT a.applicant_id, a.full_name, a.email, a.annual_income, "
               "a.employment_years, a.ssn, c.score AS credit_score, c.bureau "
               "FROM applicants a LEFT JOIN credit_scores c USING (applicant_id) "
               "WHERE a.applicant_id = %(applicant_id)s")
        return sql, _run_sql(sql, {"applicant_id": applicant_id})

    if name == "list_all_applicants":
        # No role restriction — any caller can enumerate every applicant (incl. ssn + score).
        sql = ("SELECT a.applicant_id, a.full_name, a.email, a.ssn, c.score AS credit_score "
               "FROM applicants a LEFT JOIN credit_scores c USING (applicant_id) "
               "ORDER BY a.applicant_id")
        return sql, _run_sql(sql)

    if name == "get_credit_report":
        applicant_id = int(args.get("applicant_id", 0))
        # No role restriction — raw bureau score to any caller.
        sql = ("SELECT applicant_id, score, bureau, last_updated "
               "FROM credit_scores WHERE applicant_id = %(applicant_id)s")
        return sql, _run_sql(sql, {"applicant_id": applicant_id})

    if name == "search_applicants":
        # Expression-language gateway — forwards the caller's CEL filter to SQL with
        # no allow-list, so an injected tautology dumps every applicant incl. ssn.
        where = _cel_to_sql_where(str(args.get("filter", "")))
        sql = ("SELECT applicant_id, full_name, email, annual_income, ssn "
               f"FROM applicants WHERE {where}")
        return sql, _run_sql(sql)

    if name == "decide_loan":
        loan_id  = int(args.get("loan_id", 0))
        decision = str(args.get("decision", ""))
        # No role check, no approval workflow, no ceiling — any caller decides any loan.
        sql = ("UPDATE loan_applications SET status = %(decision)s, decided_by = %(uid)s "
               "WHERE loan_id = %(loan_id)s")
        return sql, _run_sql(sql, {
            "decision": decision, "uid": caller_uid, "loan_id": loan_id,
        })

    return "", {"error": f"unknown function: {name}"}


# Public names (the leading underscore is kept internally for the SQL helpers).
TOOLS = _TOOLS
dispatch = _dispatch
