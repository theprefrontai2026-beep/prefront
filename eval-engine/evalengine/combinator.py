"""The only module that knows block > approval_required > flag > allow, and
the only place deployment mode changes behavior (Hard Rule 5).

- inline: reduce a set of verdicts for one call into one enforced effect.
  indeterminate is treated as approval_required for precedence purposes -
  drift can gate a call, never silently bypass a control (Hard Rule 6).
- oob: every verdict becomes one version-stamped Finding record (nothing is
  dropped - Hard Rule 15). An indeterminate verdict is resolved to
  missing_precondition or visibility_gap via the visibility profile
  (Hard Rule 7), never left unlabelled and never silently promoted to
  violated or satisfied.
"""

from __future__ import annotations

from .contract import EFFECT_PRECEDENCE, ConformanceTag, Effect, Finding, Verdict, VersionStamp
from .visibility import VisibilityProfile


def combine_inline(verdicts: list[Verdict]) -> tuple[Effect, list[Verdict]]:
    """Reduce many verdicts (one call, several checks) to one enforced effect."""
    if not verdicts:
        return "allow", []
    worst: Effect = "allow"
    for v in verdicts:
        effect: Effect = "approval_required" if v.status == "indeterminate" else v.effect
        if v.status == "satisfied":
            effect = "allow"
        if EFFECT_PRECEDENCE[effect] > EFFECT_PRECEDENCE[worst]:
            worst = effect
    return worst, list(verdicts)


def _resolve_indeterminate_reason(verdict: Verdict, visibility: VisibilityProfile) -> str:
    if not verdict.missing_capture:
        return "missing_precondition"
    return "missing_precondition" if visibility.captured(verdict.missing_capture) else "visibility_gap"


def combine_oob(verdicts: list[Verdict], visibility: VisibilityProfile, versions: VersionStamp,
                evaluated_at: str) -> list[Finding]:
    """Every verdict -> one version-stamped Finding (persisted to the
    `verdicts` table verbatim - see store.py). Never filters, never drops."""
    out: list[Finding] = []
    for v in verdicts:
        reason = _resolve_indeterminate_reason(v, visibility) if v.status == "indeterminate" else None
        out.append(Finding(verdict=v, versions=versions, mode="oob",
                           indeterminate_reason=reason, evaluated_at=evaluated_at))
    return out


def violations(findings: list[Finding]) -> list[Finding]:
    """The `findings` table content: violated verdicts only."""
    return [f for f in findings if f.verdict.status == "violated"]


def conformance_tags(findings: list[Finding]) -> list[ConformanceTag]:
    """The `conformance_tags` table content: satisfied-and-exercised verdicts."""
    return [
        ConformanceTag(
            session_id=f.verdict.session_id,
            check_id=f.verdict.check_id,
            rule_id=f.verdict.rule_id,
            evidence=f.verdict.evidence,
            versions=f.versions,
            source=f.verdict.source,
            evaluated_at=f.evaluated_at,
        )
        for f in findings
        if f.verdict.status == "satisfied"
    ]
