#!/usr/bin/env python3
"""Grading harness (autonomous_build.md step 15): run LoanPro's scenario
catalogue via the orchestrator, evaluate every resulting session through
eval-engine, and diff BOTH halves against scenarios.py - findings vs
expected_findings, and conformance tags vs `demonstrates` for baseline
sessions. THIS IS THE ACCEPTANCE GATE for eval-engine Phases A-B: a clean
run means the engine reproduces what the fixture says it should.

    python grading_harness.py                        # full catalogue, incl. hidden
    python grading_harness.py --only F1-01,BASE-01    # a subset
    python grading_harness.py --out ../docs/eval-coverage.md
    python grading_harness.py --lenient               # exit 0 regardless (for iterating)

Env: ORCHESTRATOR_URL (default http://localhost:8098), OOB_URL
(http://localhost:8110), EVAL_URL (http://localhost:8120).

LoanPro's role here is fixture, not dependency (autonomous_build.md §2): this
script is a TEST DEPENDENCY of the repo, never a runtime dependency of
eval-engine, which names no demo (see eval-engine/tests/test_domain_independence.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from scenarios import SCENARIOS

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8098").rstrip("/")
OOB_URL = os.environ.get("OOB_URL", "http://localhost:8110").rstrip("/")
EVAL_URL = os.environ.get("EVAL_URL", "http://localhost:8120").rstrip("/")

INGEST_TIMEOUT_S = 90
INGEST_POLL_S = 3


def _get(url: str, timeout: int = 30) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:
        return json.load(resp)


def _post(url: str, timeout: int = 60) -> Any:
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.load(e)}
        except Exception:  # noqa: BLE001
            return {"error": f"HTTP {e.code}"}


def run_catalogue(only: Optional[list[str]], variant: str = "") -> list[dict]:
    ids = only or [s["id"] for s in SCENARIOS]
    url = f"{ORCHESTRATOR_URL}/api/run?only={','.join(ids)}"
    if variant:
        url += f"&variant={variant}"
    print(f"-> running scenarios via orchestrator: {url}", file=sys.stderr)
    return _get(url, timeout=1800)


# Population checks aren't per-session (autonomous_build.md step 17) - this
# maps each POP-* scenario to the extra /eval/population call(s) it needs
# after its session(s) are evaluated. Demo-specific knowledge belongs here,
# in the fixture-side harness, never in eval-engine itself.
POP_VARIANT = {"POP-01": "v2"}
POP_DRIFT = {"POP-02": ("v1", "v2")}
POP_RULE_TREND = {"POP-03": "R-APPROVAL-OVER-50K"}


def wait_for_ingestion(session_id: str) -> bool:
    deadline = time.time() + INGEST_TIMEOUT_S
    while time.time() < deadline:
        try:
            data = _get(f"{OOB_URL}/oob/sessions/{session_id}")
            if data.get("spans"):
                return True
        except urllib.error.HTTPError:
            pass
        except Exception:  # noqa: BLE001
            pass
        time.sleep(INGEST_POLL_S)
    return False


def evaluate_session(session_id: str) -> dict:
    return _post(f"{EVAL_URL}/eval/run?session_id={session_id}&force=true")


def fetch_verdicts(session_id: str) -> list[dict]:
    return _get(f"{EVAL_URL}/eval/sessions/{session_id}/verdicts").get("verdicts", [])


def fetch_conformance(session_id: str) -> list[dict]:
    return _get(f"{EVAL_URL}/eval/sessions/{session_id}/conformance").get("conformance_tags", [])


def run_population(scenario_id: str, **params: str) -> dict:
    query = "&".join(f"{k}={v}" for k, v in {"scenario_id": scenario_id, **params}.items() if v)
    return _post(f"{EVAL_URL}/eval/population?{query}")


def fetch_population_verdicts(*keys: str) -> list[dict]:
    """`keys` are the synthetic population session_id(s) evaluate.py mints -
    "population:<scenario_id>[:<variant>]" / "population:<scenario_id>:<a>-vs-<b>"
    / "population:rule:<rule_id>". Mirrors evaluate.py's key format; the two
    are not shared code (this file is the fixture side), so keep them in
    sync by hand if that format ever changes."""
    out = []
    for key in keys:
        out.extend(_get(f"{EVAL_URL}/eval/sessions/{key}/verdicts").get("verdicts", []))
    return out


def _cited(policy_number: str, sections: list[str]) -> bool:
    pattern = re.compile(r"\b" + re.escape(policy_number) + r"\b")
    return any(pattern.search(sec or "") for sec in sections)


def grade_scenario(s: dict, verdicts: list[dict], tags: list[dict]) -> dict:
    expected = {f["check"] for f in s.get("expected_findings", [])}
    violated = {v["check_id"] for v in verdicts if v["status"] == "violated"}
    indeterminate = {v["check_id"] for v in verdicts if v["status"] == "indeterminate"}

    result: dict[str, Any] = {
        "id": s["id"], "family": s["family"], "mode": s.get("mode", "llm"),
        "baseline": bool(s.get("baseline")),
        "expected": sorted(expected),
        "matched": sorted(expected & violated),
        "matched_indeterminate": sorted(expected & indeterminate - violated),
        "missing": sorted(expected - violated - indeterminate),
        "extra_violations": sorted(violated - expected),
    }

    # A check only counts as confirmed when it actually VIOLATED - an
    # indeterminate is the engine correctly flagging "can't tell" (Hard Rule 7),
    # which deserves partial credit, never a silent PASS.
    unconfirmed = expected - violated

    if s.get("baseline"):
        demonstrated = sorted({c["policy"] for c in s.get("demonstrates", [])})
        sections = [t.get("section", "") for t in tags]
        result["demonstrates"] = demonstrated
        result["demonstrates_missing"] = [p for p in demonstrated if not _cited(p, sections)]
        result["unexpected_violations"] = sorted(violated)
        grade = "FAIL" if (result["demonstrates_missing"] or result["unexpected_violations"]) else "PASS"
    elif not expected or not unconfirmed:
        grade = "PASS"
    elif unconfirmed & indeterminate:
        grade = "PARTIAL"
    else:
        grade = "FAIL"
    result["grade"] = grade
    return result


def render_report(results: list[dict]) -> str:
    lines = [
        "# eval-engine coverage report",
        "",
        "Generated by `loanpro-demo/grading_harness.py` (autonomous_build.md step 15) -",
        "the acceptance gate for eval-engine Phases A-B. Diffs eval-engine's actual",
        "verdicts/conformance-tags for each scenario session against",
        "`scenarios.py`'s `expected_findings` / `demonstrates`.",
        "",
        "| id | family | mode | grade | expected | matched | missing | extra |",
        "|---|---|---|---|---|---|---|---|",
    ]
    by_grade = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    for r in results:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
        expected = ", ".join(r.get("demonstrates", r["expected"])) or "-"
        matched = ", ".join(r["matched"]) or ("-" if not r["baseline"] else "n/a")
        missing = ", ".join(r.get("demonstrates_missing", r["missing"])) or "-"
        extra = ", ".join(r.get("unexpected_violations", r["extra_violations"])) or "-"
        lines.append(f"| {r['id']} | {r['family']} | {r['mode']} | **{r['grade']}** | "
                     f"{expected} | {matched} | {missing} | {extra} |")
    total = len(results)
    lines.append("")
    lines.append(f"**{by_grade['PASS']}/{total} PASS, {by_grade['PARTIAL']}/{total} PARTIAL, "
                 f"{by_grade['FAIL']}/{total} FAIL.**")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated scenario ids (default: the full catalogue)")
    ap.add_argument("--out", help="write the markdown report here (default: print to stdout)")
    ap.add_argument("--json-out", help="also write the raw per-scenario results as JSON")
    ap.add_argument("--lenient", action="store_true", help="always exit 0 (for iterating on the engine)")
    args = ap.parse_args()

    only = [s.strip().upper() for s in args.only.split(",")] if args.only else None

    runs = run_catalogue(only)
    print(f"-> {len(runs)} session(s) generated", file=sys.stderr)

    by_id = {s["id"]: s for s in SCENARIOS}
    results = []
    for run in runs:
        sid = run["id"]
        session_id = run["session_id"]
        s = by_id[sid]
        print(f"-> {sid}: waiting for ingestion of session {session_id}", file=sys.stderr)
        if not wait_for_ingestion(session_id):
            print(f"   WARNING: {session_id} never showed up in OOB ingestion", file=sys.stderr)
        eval_result = evaluate_session(session_id)
        if eval_result.get("error"):
            print(f"   WARNING: eval-engine error for {session_id}: {eval_result['error']}", file=sys.stderr)
        verdicts = fetch_verdicts(session_id)
        tags = fetch_conformance(session_id)
        graded = grade_scenario(s, verdicts, tags)
        graded["session_id"] = session_id
        results.append(graded)
        print(f"   {sid}: {graded['grade']}", file=sys.stderr)

    ran_ids = {r["id"] for r in results}
    for sid in sorted(ran_ids & (POP_VARIANT.keys() | POP_DRIFT.keys() | POP_RULE_TREND.keys())):
        s = by_id[sid]
        print(f"-> {sid}: running population check(s)", file=sys.stderr)
        keys = []
        if sid in POP_VARIANT:
            run_population(sid, variant=POP_VARIANT[sid])
            keys.append(f"population:{sid}:{POP_VARIANT[sid]}")
        if sid in POP_DRIFT:
            baseline, compare = POP_DRIFT[sid]
            extra_runs = run_catalogue([sid], variant=compare)
            for run in extra_runs:
                if wait_for_ingestion(run["session_id"]):
                    evaluate_session(run["session_id"])
                else:
                    print(f"   WARNING: {run['session_id']} never showed up in OOB ingestion", file=sys.stderr)
            run_population(sid, baseline_variant=baseline, compare_variant=compare)
            keys.append(f"population:{sid}:{baseline}-vs-{compare}")
        if sid in POP_RULE_TREND:
            rule_id = POP_RULE_TREND[sid]
            run_population(sid, rule_id=rule_id)
            keys.append(f"population:rule:{rule_id}")
        pop_verdicts = fetch_population_verdicts(*keys)
        graded = grade_scenario(s, pop_verdicts, [])
        graded["session_id"] = "/".join(keys)
        # this scenario already has a per-session PASS/FAIL entry from the main
        # loop above (its own session ran a single instance too) - replace it
        # with the population-informed grade rather than keeping both.
        results = [r for r in results if r["id"] != sid] + [graded]
        print(f"   {sid}: {graded['grade']} (population)", file=sys.stderr)

    report = render_report(results)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"-> wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2), encoding="utf-8")

    failed = [r for r in results if r["grade"] == "FAIL"]
    if args.lenient:
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
