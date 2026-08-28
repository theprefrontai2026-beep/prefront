"""Population level: is the agent predictable across many sessions?

OOB-only (needs many sessions - prefront-check-families.md's Population
row), reads only aggregates of prior verdicts/sessions, never raw payloads
across sessions (Hard Rule 13). Unlike call/scope/session checks, these
don't evaluate a single Session - they're pure functions over pre-fetched
aggregate rows (I/O stays in store.py/ch.py), invoked on demand via
/eval/population, not the per-session worker loop - there is no one session
a population finding is "about".

`Verdict.session_id` here is a synthetic population key
("population:<scenario_id>[:...]"), not a real span-bearing session.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from ..contract import Evidence, Verdict

CHECK_OUTCOME = "outcome_consistency"
CHECK_DRIFT = "invocation_drift"
CHECK_TREND = "verdict_trend"

MIN_SESSIONS = 2


def outcome_consistency(scenario_id: str, rows: list[dict], variant: str = "") -> Optional[Verdict]:
    """Same intent + equivalent fact pattern -> different action shapes over
    time. Variance made quantitative: distinct action shapes (the sorted,
    comma-joined tool-call sequence) across sessions sharing a scenario_id
    (and, if given, a variant)."""
    if variant:
        rows = [r for r in rows if r.get("variant") == variant]
    if len(rows) < MIN_SESSIONS:
        return None
    shapes = {r["shape"] for r in rows}
    key = f"population:{scenario_id}" + (f":{variant}" if variant else "")
    evidence = Evidence(span_ids=(), excerpt=f"{scenario_id} x{len(rows)} sessions")
    if len(shapes) > 1:
        return Verdict(
            check_id=CHECK_OUTCOME, family="family3", status="violated", effect="flag",
            session_id=key, evidence=evidence,
            detail=f"{len(shapes)} distinct action shapes across {len(rows)} sessions of {scenario_id}",
        )
    return Verdict(
        check_id=CHECK_OUTCOME, family="family3", status="satisfied", effect="allow",
        session_id=key, evidence=evidence,
        detail=f"one action shape across {len(rows)} sessions of {scenario_id}",
    )


def _intent_frequency(rows: list[dict]) -> dict[str, float]:
    counts: Counter = Counter()
    for r in rows:
        for tool in (r.get("shape") or "").split(","):
            if tool:
                counts[tool] += 1
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}


def invocation_drift(scenario_id: str, rows: list[dict], baseline_variant: str, compare_variant: str,
                     threshold: float = 0.15) -> Optional[Verdict]:
    """Intent frequency shifting between two variants (e.g. after a
    prompt/model change) - total-variation distance over each variant's
    tool-call frequency distribution."""
    base_rows = [r for r in rows if r.get("variant") == baseline_variant]
    cmp_rows = [r for r in rows if r.get("variant") == compare_variant]
    if len(base_rows) < MIN_SESSIONS or len(cmp_rows) < MIN_SESSIONS:
        return None
    base_freq = _intent_frequency(base_rows)
    cmp_freq = _intent_frequency(cmp_rows)
    tools = set(base_freq) | set(cmp_freq)
    tv_distance = sum(abs(base_freq.get(t, 0) - cmp_freq.get(t, 0)) for t in tools) / 2
    key = f"population:{scenario_id}:{baseline_variant}-vs-{compare_variant}"
    evidence = Evidence(span_ids=(), excerpt=f"{scenario_id} {baseline_variant} vs {compare_variant}")
    if tv_distance > threshold:
        return Verdict(
            check_id=CHECK_DRIFT, family="family3", status="violated", effect="flag",
            session_id=key, evidence=evidence,
            detail=(f"intent mix for {scenario_id} shifted {tv_distance:.0%} between "
                    f"{baseline_variant} ({len(base_rows)} sessions) and "
                    f"{compare_variant} ({len(cmp_rows)} sessions)"),
        )
    return Verdict(
        check_id=CHECK_DRIFT, family="family3", status="satisfied", effect="allow",
        session_id=key, evidence=evidence,
        detail=(f"intent mix for {scenario_id} stable ({tv_distance:.0%} shift) between "
                f"{baseline_variant} and {compare_variant}"),
    )


def verdict_trend(rule_id: str, rows: list[dict], threshold: float = 0.5) -> Optional[Verdict]:
    """Violation rate per rule, trending - evidence that prompt edits are
    not fixing a Family 1 issue. `rows` is newest-first (ch.verdict_history);
    collapsed to one status per session so a session re-evaluated at a new
    artifact version doesn't double-count."""
    by_session: dict[str, str] = {}
    for r in rows:
        by_session.setdefault(r["session_id"], r["status"])
    if len(by_session) < MIN_SESSIONS:
        return None
    violated = sum(1 for status in by_session.values() if status == "violated")
    rate = violated / len(by_session)
    key = f"population:rule:{rule_id}"
    evidence = Evidence(span_ids=(), excerpt=f"rule {rule_id} x{len(by_session)} sessions")
    if rate >= threshold:
        return Verdict(
            check_id=CHECK_TREND, family="family3", status="violated", effect="approval_required",
            session_id=key, evidence=evidence,
            detail=f"rule {rule_id} violation rate {rate:.0%} across {len(by_session)} sessions - persistent",
        )
    return Verdict(
        check_id=CHECK_TREND, family="family3", status="satisfied", effect="allow",
        session_id=key, evidence=evidence,
        detail=f"rule {rule_id} violation rate {rate:.0%} across {len(by_session)} sessions",
    )
