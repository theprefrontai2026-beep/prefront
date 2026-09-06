#!/usr/bin/env python3
"""Score a MINED catalog against a hand-authored one — intent_learning_design.md §6.

A miner nobody has measured is a guess with a JSON schema. LoanPro is the ideal
holdout: it has a hand-authored intent_catalog.yaml AND the traces the miner
reads, so every "learnable" claim in the design's §3 table can be checked
against ground truth rather than asserted.

Alignment uses `app.intent` — the label LoanPro stamps on its spans — purely to
match a mined tool to the authored entry for the same operation. That label is
the ANSWER KEY and is never a mining input (see evalengine/behavior's module
docstring); using it here is scoring, not cheating, and doing it any other way
would mean matching on names the miner invented.

    python3 score_mined_catalog.py <mined.json> <intent_catalog.yaml>
"""
from __future__ import annotations

import json
import sys
import urllib.request

import yaml

EVAL = "http://localhost:8120"


def prf(mined: set, authored: set) -> tuple[float, float, int, int, int]:
    tp = len(mined & authored)
    p = tp / len(mined) if mined else (1.0 if not authored else 0.0)
    r = tp / len(authored) if authored else 1.0
    return p, r, tp, len(mined - authored), len(authored - mined)


def main() -> int:
    mined = json.load(open(sys.argv[1]))
    doc = yaml.safe_load(open(sys.argv[2]))
    # The file wraps its body in a single top-level key, as every artifact in
    # this repo does; tolerate both so a bare body scores too.
    body = doc.get("intent_catalog") or doc
    authored = {e["intent"]: e for e in (body.get("intents") or [])}
    if not authored:
        print(f"!! no intents parsed from {sys.argv[2]} — nothing to score against",
              file=sys.stderr)
        return 2

    with urllib.request.urlopen(f"{EVAL}/eval/behavior/labels?since=2592000&app=loanpro") as r:
        labels = json.load(r)["labels"]
    # tool -> the intent name the deployment stamps most often for it
    tool_intent = {
        t: max((x for x in xs if x["intent"]), key=lambda x: x["calls"])["intent"]
        for t, xs in labels.items() if any(x["intent"] for x in xs)
    }

    print(f"mined {len(mined)} candidates; authored catalog has {len(authored)} intents\n")
    hdr = f"{'tool':<26} {'aligned intent':<26} {'params':>14} {'fields':>14} {'side':>6} {'roles P/R':>12}"
    print(hdr); print("-" * len(hdr))

    agg = {"params": [0, 0, 0], "fields": [0, 0, 0], "roles": [0, 0, 0]}
    side_ok = side_n = aligned = 0
    for c in mined:
        tool = c["tool_name"]
        name = tool_intent.get(tool, "")
        a = authored.get(name)
        if not a:
            print(f"{tool:<26} {'(no authored match)':<26}")
            continue
        aligned += 1
        row = [f"{tool:<26}", f"{name:<26}"]
        for field, mset, aset in (
            ("params", set(c["params"]), set(a.get("params") or [])),
            ("fields", set(c["fields"]), set(a.get("fields") or [])),
            # Authored roles are nested under allowed_callers; mined roles are OBSERVED,
            # so a mined role absent from the authored set is the design's
            # "observed != allowed" gap showing up as precision loss, which is the
            # number worth knowing rather than an error to hide.
            ("roles", {r["value"] for r in c["observed_roles"]},
             set((a.get("allowed_callers") or {}).get("roles") or a.get("allowed_roles") or [])),
        ):
            p, r_, tp, fp, fn = prf(mset, aset)
            agg[field][0] += tp; agg[field][1] += fp; agg[field][2] += fn
            row.append(f"{p:.2f}/{r_:.2f}".rjust(14 if field != "roles" else 12))
        ok = c["side_effect"] == (a.get("side_effect") or "read")
        side_ok += ok; side_n += 1
        row.insert(4, ("ok" if ok else "MISS").rjust(6))
        print(" ".join(row))

    # Refuse to print a score off nothing. An empty set scores 1.00/1.00 by
    # definition, so a scorer that reports it announces a perfect result for a
    # total failure to align — the same vacuous-pass shape as a harness grading
    # a runtime that is not running.
    if not aligned:
        print("\n!! NOTHING ALIGNED — no mined tool matched an authored intent.\n"
              "   The precision/recall below would be computed over empty sets and\n"
              "   would read 1.00/1.00, which would be meaningless. Check that the\n"
              "   catalog path is right and that /eval/behavior/labels is populated.",
              file=sys.stderr)
        return 2
    print(f"\naligned {aligned} of {len(mined)} mined candidates\n")
    print(f"{'field':<10} {'precision':>10} {'recall':>10}   (tp/fp/fn)")
    for f, (tp, fp, fn) in agg.items():
        p = tp / (tp + fp) if tp + fp else 1.0
        r = tp / (tp + fn) if tp + fn else 1.0
        print(f"{f:<10} {p:>10.2f} {r:>10.2f}   ({tp}/{fp}/{fn})")
    print(f"{'side_effect':<10} {side_ok}/{side_n} exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
