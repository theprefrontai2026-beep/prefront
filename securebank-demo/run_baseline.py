#!/usr/bin/env python3
"""SecureBank — UNGOVERNED baseline runner ("before").

Deterministic stand-in for an ungoverned AI agent wired straight at the database:
for each scenario it runs the SQL the agent would run (read-only), with NO
identity scoping, NO field masking, NO policy. The output is the "before"
evidence — PII leaks, cross-customer reads, unconstrained actions — that the
governed run (Phase 2) will contrast against.

No LLM, no Prefront. Just Postgres.

    docker compose up -d
    python3 run_baseline.py                 # all scenarios -> artifacts/baseline_*.{json,md}
    python3 run_baseline.py --only B3,B4     # subset
    python3 run_baseline.py --list           # questions only, no DB
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import os
import sys
from pathlib import Path

from scenarios import CALLERS, get_scenarios

DSN = os.environ.get(
    "SECUREBANK_DSN",
    "postgresql://securebank:securebank@localhost:5434/securebank",
)
ARTIFACTS = Path(__file__).parent / "artifacts"
MAX_ROWS_SHOWN = 8


def _json_default(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def _run_sql(conn, sql: str) -> dict:
    """Run a read-only query; return {columns, rows, row_count} or {error}."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"columns": cols, "rows": rows, "row_count": len(rows)}
    except Exception as e:  # noqa: BLE001 — surface any DB error verbatim
        conn.rollback()
        return {"error": f"{type(e).__name__}: {e}"}


def run(scenarios) -> list[dict]:
    import psycopg

    results = []
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.read_only = True  # the baseline only reads; writes are described, not run
        for s in scenarios:
            caller = CALLERS[s["caller"]]
            rec = {
                "id": s["id"], "capability": s["capability"],
                "caller": caller["name"], "role": caller["role"],
                "question": s["question"], "sql": s.get("sql"),
                "risk": s["risk"], "prefront": s["prefront"],
            }
            if s.get("would"):
                rec["ungoverned_action"] = s["would"]
            if s.get("sql"):
                rec.update(_run_sql(conn, s["sql"]))
            else:
                rec["note"] = "no real data/intent — an ungoverned agent fabricates an answer"
            results.append(rec)
    return results


def _print_report(results: list[dict]) -> None:
    for r in results:
        print(f"\n── {r['id']} · {r['capability']} · {r['caller']} ({r['role']}) "
              + "─" * 8)
        print(f"   Q: {r['question']}")
        if r.get("error"):
            print(f"   ungoverned: ERROR {r['error']}")
        elif "row_count" in r:
            print(f"   ungoverned: returned {r['row_count']} row(s) — "
                  f"columns: {', '.join(r['columns'])}")
            for row in r["rows"][:MAX_ROWS_SHOWN]:
                print(f"       {json.dumps(row, default=_json_default)}")
            if r["row_count"] > MAX_ROWS_SHOWN:
                print(f"       … {r['row_count'] - MAX_ROWS_SHOWN} more")
        else:
            print(f"   ungoverned: {r.get('note', '')}")
        if r.get("ungoverned_action"):
            print(f"   ungoverned would: {r['ungoverned_action']}")
        print(f"   ⚠ risk:     {r['risk']}")
        print(f"   ✓ prefront: {r['prefront']}")


def _write_artifacts(results: list[dict]) -> tuple[Path, Path]:
    ARTIFACTS.mkdir(exist_ok=True)
    jpath = ARTIFACTS / "baseline_run.json"
    jpath.write_text(json.dumps(results, indent=2, default=_json_default), encoding="utf-8")

    lines = ["# SecureBank — ungoverned baseline (\"before\")\n",
             "The ungoverned agent runs raw SQL with no identity, masking, or policy.\n"]
    for r in results:
        lines.append(f"\n## {r['id']} · {r['capability']} — {r['caller']} ({r['role']})\n")
        lines.append(f"**Request:** {r['question']}\n")
        if r.get("sql"):
            lines.append(f"```sql\n{r['sql']}\n```\n")
        if r.get("error"):
            lines.append(f"- Ungoverned: `ERROR {r['error']}`\n")
        elif "row_count" in r:
            lines.append(f"- Ungoverned: **{r['row_count']} row(s)** returned "
                         f"({', '.join(r['columns'])})\n")
        else:
            lines.append(f"- Ungoverned: {r.get('note','')}\n")
        if r.get("ungoverned_action"):
            lines.append(f"- Ungoverned would: {r['ungoverned_action']}\n")
        lines.append(f"- ⚠ Risk: {r['risk']}\n")
        lines.append(f"- ✓ Prefront: {r['prefront']}\n")
    mpath = ARTIFACTS / "baseline_report.md"
    mpath.write_text("".join(lines), encoding="utf-8")
    return jpath, mpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SecureBank ungoverned baseline runner")
    ap.add_argument("--only", help="comma-separated scenario ids, e.g. B3,B4")
    ap.add_argument("--list", action="store_true", help="print questions only (no DB)")
    args = ap.parse_args(argv)

    scenarios = get_scenarios(args.only.split(",") if args.only else None)
    if not scenarios:
        print("no scenarios matched", file=sys.stderr)
        return 1

    if args.list:
        for s in scenarios:
            c = CALLERS[s["caller"]]
            print(f"{s['id']:>4}  {c['name']:<12} ({c['role']:<14})  {s['question']}")
        return 0

    results = run(scenarios)
    _print_report(results)
    jpath, mpath = _write_artifacts(results)
    print(f"\nWrote {jpath} and {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
