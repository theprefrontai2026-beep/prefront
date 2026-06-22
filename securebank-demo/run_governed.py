#!/usr/bin/env python3
"""SecureBank — GOVERNED runner ("after").

Deterministic stand-in for the SAME requests routed through the Prefront runtime.
For each scenario it calls the approved intent (with explicit args — no LLM)
through the real governance pipeline IN-PROCESS (`call_governed`): identity is
resolved from the trusted config (never the agent), the policy rules evaluate
against the precheck row + args + caller context, and the call is allowed,
blocked, masked, or routed for approval. Writes are dry-run unless ENABLE_WRITES=1.

No MCP servers, no SSE, no LLM — the governed decision, reproducibly.

    docker compose up -d
    python3 run_governed.py                 # all scenarios -> artifacts/governed_*.{json,md}
    python3 run_governed.py --only B5,B8     # subset

Run it with the engine venv (it has `semanticmcp` + psycopg), e.g.:
    ../prefront/semantic-mcp-server/.venv/bin/python run_governed.py
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

HERE = Path(__file__).parent
# The engine runtime package (governed pipeline) lives in the sibling repo.
ENGINE = HERE.parent / "prefront" / "semantic-mcp-server"
sys.path.insert(0, str(ENGINE))

DSN = os.environ.get(
    "SECUREBANK_DSN",
    "postgresql://securebank:securebank@localhost:5434/securebank",
)
TEMPLATES = HERE / "policy" / "query_templates.yaml"
POLICY = HERE / "policy" / "policy.yaml"
ARTIFACTS = HERE / "artifacts"
# Map a caller (ACT_AS=email) to its attributes (caller.user_id, caller.role, …).
IDENTITY_QUERY = "SELECT user_id, name, email, role FROM users WHERE email = :who"


def _json_default(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def _classify(scn: dict, result: dict) -> str:
    """One-word outcome for the side-by-side, from the governance result."""
    if result.get("_no_intent"):
        return "BLOCK (no approved intent)"
    status = result.get("status")
    if status == "blocked":
        return "BLOCK (policy)"
    if status == "approval_required":
        return "APPROVAL (policy)"
    if status == "allowed":
        if result.get("masked_fields"):
            return "ALLOW (fields masked)"
        # A scoped single-record read that returns nothing = out of the caller's scope.
        if scn.get("args", {}).get("account_id") and not result.get("row_count"):
            return "BLOCK (out of scope)"
        return "ALLOW (scoped to caller)"
    return status or "UNKNOWN"


def run(scenarios) -> list[dict]:
    from semanticmcp.governance import PolicyRegistry
    from semanticmcp.server import call_governed, load_templates

    os.environ["IDENTITY_QUERY"] = IDENTITY_QUERY
    os.environ["POLICY_PATH"] = str(POLICY)
    os.environ.setdefault("DATABASE_URL", DSN)

    tools = load_templates(TEMPLATES, governed=True)
    policy = PolicyRegistry(str(POLICY))
    policy.refresh()

    results = []
    for s in scenarios:
        caller = CALLERS[s["caller"]]
        rec = {
            "id": s["id"], "capability": s["capability"],
            "caller": caller["name"], "role": caller["role"],
            "question": s["question"], "risk": s["risk"], "prefront_expected": s["prefront"],
            "intent": s.get("intent"), "args": s.get("args", {}),
        }
        intent = s.get("intent")
        tool = tools.get(intent) if intent else None
        if tool is None:
            # No approved intent — the gateway/validator has nothing to run.
            rec["result"] = {"_no_intent": True,
                             "status": "blocked",
                             "reasons": ["no_approved_intent: the request maps to no "
                                         "governed tool (raw SQL / fabrication / bulk export)"]}
        else:
            os.environ["ACT_AS"] = caller["email"]  # cache re-resolves per caller
            rec["result"] = call_governed(tool, DSN, dict(s.get("args", {})), policy)
        rec["outcome"] = _classify(s, rec["result"])
        results.append(rec)
    return results


def _print_report(results: list[dict]) -> None:
    for r in results:
        res = r["result"]
        print(f"\n── {r['id']} · {r['capability']} · {r['caller']} ({r['role']}) "
              + "─" * 8)
        print(f"   Q: {r['question']}")
        print(f"   intent: {r['intent'] or '(none)'}   args: {json.dumps(r['args'], default=_json_default)}")
        print(f"   ▶ governed: {r['outcome']}")
        if res.get("reasons"):
            for reason in res["reasons"]:
                print(f"       reason: {reason}")
        if res.get("approver_roles"):
            print(f"       approver: {', '.join(res['approver_roles'])}")
        if res.get("masked_fields"):
            print(f"       masked: {', '.join(res['masked_fields'])}")
        if res.get("status") == "allowed" and res.get("rows") is not None:
            print(f"       rows: {res.get('row_count')} returned")
            for row in (res.get("rows") or [])[:4]:
                print(f"         {json.dumps(row, default=_json_default)}")
        if res.get("write"):
            w = res["write"]
            print(f"       write: {w.get('mode')} → {w.get('table') or ''} "
                  f"{json.dumps({k: v for k, v in w.items() if k not in ('mode', 'note')}, default=_json_default)}")
        print(f"   (expected: {r['prefront_expected']})")


def _write_artifacts(results: list[dict]) -> tuple[Path, Path]:
    ARTIFACTS.mkdir(exist_ok=True)
    jpath = ARTIFACTS / "governed_run.json"
    jpath.write_text(json.dumps(results, indent=2, default=_json_default), encoding="utf-8")

    lines = ["# SecureBank — governed run (\"after\")\n",
             "The same requests routed through the Prefront runtime: identity is "
             "injected (not agent-supplied), and policy decides every call.\n"]
    for r in results:
        res = r["result"]
        lines.append(f"\n## {r['id']} · {r['capability']} — {r['caller']} ({r['role']})\n")
        lines.append(f"**Request:** {r['question']}\n")
        lines.append(f"- Intent: `{r['intent'] or '(none)'}`  ·  args: `{json.dumps(r['args'], default=_json_default)}`\n")
        lines.append(f"- **Governed: {r['outcome']}**\n")
        for reason in res.get("reasons") or []:
            lines.append(f"  - reason: {reason}\n")
        if res.get("approver_roles"):
            lines.append(f"  - approver: {', '.join(res['approver_roles'])}\n")
        if res.get("masked_fields"):
            lines.append(f"  - masked: {', '.join(res['masked_fields'])}\n")
        lines.append(f"- Expected: {r['prefront_expected']}\n")
    mpath = ARTIFACTS / "governed_report.md"
    mpath.write_text("".join(lines), encoding="utf-8")
    return jpath, mpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SecureBank governed runner")
    ap.add_argument("--only", help="comma-separated scenario ids, e.g. B5,B8")
    args = ap.parse_args(argv)

    scenarios = get_scenarios(args.only.split(",") if args.only else None)
    if not scenarios:
        print("no scenarios matched", file=sys.stderr)
        return 1

    results = run(scenarios)
    _print_report(results)
    jpath, mpath = _write_artifacts(results)
    print(f"\nWrote {jpath} and {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
