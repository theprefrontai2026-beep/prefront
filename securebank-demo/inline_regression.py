#!/usr/bin/env python3
"""Inline-governance regression for SecureBank — the in-band counterpart to
LoanPro's out-of-band `grading_harness.py`.

The two demos exercise opposite halves of Prefront and had very different
coverage: LoanPro's OOB path has a 39-scenario graded harness wired into
`make grade-loanpro`, while SecureBank's INLINE path — the one where Prefront
actually sits in the request and blocks, masks or routes for approval — had no
regression at all. A governed decision could silently change and nothing would
notice.

This runs the catalogue through `securebank-orchestrator`'s `/api/diff` (which
drives the real governed agent against the real `securebank-mcp`, against the
real Postgres) and grades each scenario's GOVERNED outcome against what the
fixture says Prefront should do.

    python3 inline_regression.py                 # whole catalogue
    python3 inline_regression.py --only B2,B4    # a subset
    python3 inline_regression.py --lenient       # exit 0 regardless

Env: ORCHESTRATOR_URL (default http://localhost:8095).

ON THE EXPECTATION SOURCE, because it is weaker than LoanPro's and that should
not be discovered later: `scenarios.py` states the expected outcome as PROSE in
its `prefront` field ("BLOCK — OWN_DATA_ONLY: ..."), not as a structured
verdict the way LoanPro's `expected_findings` does. This harness therefore
grades on the leading VERDICT TOKEN of that prose and ignores the explanation
after the dash. That is enough to catch a governed decision flipping — the
regression this exists to prevent — but it will NOT catch a decision that stays
BLOCK for a different reason. Making it stronger means adding a structured
field to `scenarios.py`, which is a fixture change and is deliberately not made
here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8095").rstrip("/")

# The four decisions the runtime can produce, and the words either side uses
# for them. `decide.py`'s precedence is block > approval_required > allow, with
# masking an effect applied to an allowed call rather than a fifth decision.
ALLOW, BLOCK, MASK, APPROVAL = "allow", "block", "mask", "approval"


def _verdicts(text: str) -> set[str]:
    """The verdict class(es) a phrase names.

    Returns a SET because the fixture legitimately writes "MASK/BLOCK" where
    either satisfies the policy — masking the field and refusing the call are
    both correct answers to "a Teller must not see an SSN", and the fixture
    should not have to pick one to make a test pass.
    """
    head = re.split(r"[—\-–:]", text or "", 1)[0].upper()
    out: set[str] = set()
    if "BLOCK" in head or "DENY" in head:
        out.add(BLOCK)
    if "MASK" in head or "REDACT" in head:
        out.add(MASK)
    if "APPROV" in head:
        out.add(APPROVAL)
    if "ALLOW" in head or "PERMIT" in head:
        out.add(ALLOW)
    return out


def actual_verdict(governed: dict) -> str:
    """The verdict this run actually produced.

    Read from the structured fields, never the display string: `outcome` is
    prose for a human ("BLOCK (policy)", "ALLOW · MASKED") and grading on it
    would make a copy edit look like a regression. `status` is the decision;
    masking is an EFFECT on an allowed call, so a non-empty `masked_fields` on
    an allowed decision is the mask verdict.
    """
    status = str(governed.get("status") or "").lower()
    if status.startswith("block"):
        return BLOCK
    if "approval" in status:
        return APPROVAL
    if governed.get("masked_fields"):
        return MASK
    if status in ("allowed", "executed", "write_executed", "write_dry_run", "ok"):
        return ALLOW
    return status or "unknown"


def fetch(only: list[str] | None) -> list[dict]:
    url = f"{ORCHESTRATOR_URL}/api/diff"
    if only:
        url += "?only=" + ",".join(only)
    print(f"-> running the catalogue via {url}", file=sys.stderr)
    try:
        with urllib.request.urlopen(url, timeout=900) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"orchestrator HTTP {e.code}: {e.read()[:400]!r}")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"orchestrator unreachable at {ORCHESTRATOR_URL}: {type(e).__name__}: {e}")


def grade(row: dict) -> dict:
    governed = row.get("governed") or {}
    expected = _verdicts(row.get("expected") or "")
    got = actual_verdict(governed)
    err = governed.get("error")

    if err:
        verdict = "ERROR"
    elif not expected:
        # The fixture states no expectation for this scenario. Reported, never
        # silently counted as a pass — an unasserted scenario is not a passing
        # one, and treating it as such is how coverage quietly rots.
        verdict = "NO EXPECTATION"
    else:
        verdict = "PASS" if got in expected else "FAIL"

    return {
        "id": row.get("id"),
        "caller": row.get("caller"),
        "capability": row.get("capability"),
        "intent": governed.get("intent"),
        "expected": sorted(expected) or ["(none stated)"],
        "got": got,
        "masked": governed.get("masked_fields") or [],
        "reasons": governed.get("reasons") or [],
        "error": err,
        "grade": verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated scenario ids (default: the whole catalogue)")
    ap.add_argument("--json-out", help="also write the raw per-scenario results as JSON")
    ap.add_argument("--lenient", action="store_true", help="always exit 0 (for iterating)")
    args = ap.parse_args()

    only = [s.strip().upper() for s in args.only.split(",")] if args.only else None
    rows = fetch(only)
    results = [grade(r) for r in rows]

    width = max((len(str(r["id"] or "")) for r in results), default=2)
    for r in results:
        mark = {"PASS": "   ", "FAIL": ">> ", "ERROR": "!! ", "NO EXPECTATION": " ? "}[r["grade"]]
        print(f"{mark}{str(r['id']):<{width}}  {r['grade']:<14} "
              f"expected={'/'.join(r['expected'])} got={r['got']}", file=sys.stderr)
        if r["grade"] in ("FAIL", "ERROR"):
            for line in (r["reasons"] or [r["error"] or ""]):
                if line:
                    print(f"        {line}", file=sys.stderr)

    passed = sum(1 for r in results if r["grade"] == "PASS")
    failed = [r for r in results if r["grade"] in ("FAIL", "ERROR")]
    unasserted = [r for r in results if r["grade"] == "NO EXPECTATION"]

    print(f"\n{passed}/{len(results)} PASS, {len(failed)} FAIL/ERROR, "
          f"{len(unasserted)} with no stated expectation", file=sys.stderr)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"-> wrote {args.json_out}", file=sys.stderr)

    if args.lenient:
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
