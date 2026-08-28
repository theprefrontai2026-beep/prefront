#!/usr/bin/env python3
"""Preflight import (autonomous_build.md step 19, the second half): calls the
real Preflight generator endpoint against LoanPro's own published policy
artifacts, converts approved candidates into scenarios.py's own dict shape,
and writes them to policy/preflight_approved.json - which scenarios.py's
get_scenarios() merges in automatically. This is the missing link step 19's
own text calls for: "schema-validate -> human approve -> run through the
SAME orchestrator + grading harness in UAT" - preflight.py only builds and
validates candidates; this script is what actually lets one run.

    python preflight_import.py generate                    # LLM call, print candidates
    python preflight_import.py generate --out /tmp/c.json   # also save raw candidates
    python preflight_import.py approve /tmp/c.json PF-01 PF-03   # human approval step
    python preflight_import.py list                         # what's currently approved

Env: SEMANTIC_LAYER_URL (default http://localhost:8010).

LoanPro's role here is fixture, not dependency (same posture as
grading_harness.py's own docstring) - this script is a TEST/demo dependency
of this repo, never a runtime dependency of semantic-layer or eval-engine,
neither of which names a demo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from scenarios import CALLERS

SEMANTIC_LAYER_URL = os.environ.get("SEMANTIC_LAYER_URL", "http://localhost:8010").rstrip("/")
POLICY_DIR = Path(__file__).parent / "policy"
APPROVED_PATH = Path(os.environ.get("PREFLIGHT_SCENARIOS_PATH", str(POLICY_DIR / "preflight_approved.json")))


def _post(url: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _tools_from_catalog(catalog_doc: dict) -> list[dict]:
    """One McpTool-shaped dict per intent_catalog.yaml entry - the same
    "a tool already IS the operation" framing semantic-layer's own MCP
    connector uses (see root CLAUDE.md's "Generic MCP data-source connector"
    section), built directly from LoanPro's catalog rather than a live MCP
    introspection round-trip (LoanPro's tools aren't served as a Prefront MCP
    server at all - app_mcp_server.py is a plain, ungoverned MCP)."""
    tools = []
    for entry in catalog_doc.get("intents", []):
        tools.append({
            "tool_name": entry["tool_name"],
            "allowed_roles": entry.get("allowed_callers", {}).get("roles", []),
            "input_schema": {
                "type": "object",
                "properties": {p: {"type": "string"} for p in entry.get("params", [])},
            },
        })
    return tools


def generate(out_path: str | None) -> list[dict]:
    catalog_doc = yaml.safe_load((POLICY_DIR / "intent_catalog.yaml").read_text())["intent_catalog"]
    tools = _tools_from_catalog(catalog_doc)
    resp = _post(f"{SEMANTIC_LAYER_URL}/design/semantic/preflight/generate",
                {"tools": tools, "catalog": {"intent_catalog": catalog_doc}})
    candidates = resp.get("candidates", [])
    rejected = resp.get("rejected", [])
    print(f"-> {len(candidates)} candidate(s), {len(rejected)} rejected", file=sys.stderr)
    for r in rejected:
        print(f"   rejected: {r}", file=sys.stderr)
    for c in candidates:
        print(f"   {c['id']} [{c['family']}] {c['title']!r} checks={c['checks']} "
             f"mode={c['mode']} review_status={c['review_status']}", file=sys.stderr)
    if out_path:
        Path(out_path).write_text(json.dumps(candidates, indent=2))
        print(f"-> wrote {out_path}", file=sys.stderr)
    return candidates


def _caller_key(caller_role: str, channel: str) -> str | None:
    """Map a candidate's (role, channel) onto one of scenarios.py's named
    CALLERS - the shorthand key the rest of the harness (demo_server.py,
    grading_harness.py) already expects a scenario's `caller` field to be.
    Exact role+channel match preferred; falls back to role-only."""
    by_role_channel = [k for k, c in CALLERS.items() if c["role"] == caller_role and c["channel"] == channel]
    if by_role_channel:
        return by_role_channel[0]
    by_role = [k for k, c in CALLERS.items() if c["role"] == caller_role]
    return by_role[0] if by_role else None


def to_scenario_dict(candidate: dict) -> dict:
    """CandidateScenario's shape -> scenarios.py's dict shape. Pure, no I/O -
    the same "known shape in, known shape out" posture as skill-builder's
    rulepack.py lowering. Raises ValueError on a candidate this fixture has
    no caller for, rather than guessing one (a human already approved this
    candidate's role/channel; if nothing here matches, that's a real gap in
    CALLERS, not something to silently paper over)."""
    caller = _caller_key(candidate["caller_role"], candidate.get("channel", ""))
    if caller is None:
        raise ValueError(f"{candidate['id']}: no CALLERS entry for role {candidate['caller_role']!r}")
    out = {
        "id": candidate["id"], "family": candidate["family"], "title": candidate["title"],
        "baseline": False, "checks": candidate.get("checks", []), "caller": caller,
        "mode": candidate.get("mode", "llm"), "turns": candidate.get("turns", []),
        "risk": candidate.get("risk", ""),
        "expected_findings": [
            {k: v for k, v in f.items() if v not in (None, "")}
            for f in candidate.get("expected_findings", [])
        ],
        # Metadata the CALLER attaches, not an eval-engine concept (Hard Rule
        # 1) - preflight.py's own documented convention: "capability, not
        # incidence" findings are labelled here, not on the Verdict/Finding.
        "preflight": True,
    }
    if candidate.get("mode") == "replay":
        out["steps"] = [{"tool": s["tool"], "args": s.get("args", {})} for s in candidate.get("steps", [])]
    return out


def approve(candidates_path: str, ids: list[str]) -> None:
    candidates = json.loads(Path(candidates_path).read_text())
    wanted = {i.strip().upper() for i in ids}
    chosen = [c for c in candidates if c["id"].upper() in wanted]
    missing = wanted - {c["id"].upper() for c in chosen}
    if missing:
        raise SystemExit(f"not found in {candidates_path}: {sorted(missing)}")

    existing = json.loads(APPROVED_PATH.read_text()) if APPROVED_PATH.exists() else []
    existing_ids = {s["id"] for s in existing}
    new = [to_scenario_dict(c) for c in chosen if c["id"] not in existing_ids]
    APPROVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVED_PATH.write_text(json.dumps(existing + new, indent=2))
    print(f"-> approved {[s['id'] for s in new]}, wrote {APPROVED_PATH} "
         f"({len(existing) + len(new)} total approved)", file=sys.stderr)


def list_approved() -> None:
    if not APPROVED_PATH.exists():
        print("(none approved yet)")
        return
    for s in json.loads(APPROVED_PATH.read_text()):
        print(f"{s['id']} [{s['family']}] {s['title']!r} caller={s['caller']} mode={s['mode']} "
             f"checks={s['checks']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="call the real Preflight LLM endpoint")
    g.add_argument("--out", help="also save the raw candidate JSON here")

    a = sub.add_parser("approve", help="convert + persist chosen candidates")
    a.add_argument("candidates_path")
    a.add_argument("ids", nargs="+")

    sub.add_parser("list", help="show currently-approved Preflight scenarios")

    args = ap.parse_args(argv)
    if args.command == "generate":
        generate(args.out)
    elif args.command == "approve":
        approve(args.candidates_path, args.ids)
    elif args.command == "list":
        list_approved()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
