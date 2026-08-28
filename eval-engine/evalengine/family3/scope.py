"""Scope level: approved work, in approved shape?

field_scope | filter_scope | volume_scope. Applicable only to on-catalog
calls (an off-catalog call is catalog_membership's problem, not this
module's - Hard Rule 16).
"""

from __future__ import annotations

import re

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten
from .catalog import IntentCatalog, IntentEntry

CHECK_FIELD = "field_scope"
CHECK_FILTER = "filter_scope"
CHECK_VOLUME = "volume_scope"

VOLUME_MULTIPLE = 3.0
VOLUME_ABS_SLACK = 5

# Only ONE mandatory_filter shape is machine-checkable from free text without
# guessing: "<field> = caller" (the row must be scoped to the caller's own
# identity). Anything else (placeholders like "<subject>", prose like "loan_id
# = <own application> for an Applicant") is intentionally left unparsed -
# never guessed - so filter_scope stays silent (not applicable) on it rather
# than emit a verdict it can't actually back.
_CALLER_FILTER = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*caller\s*$", re.IGNORECASE)


def _observed_fields(step) -> set[str]:
    if step.columns:
        return set(step.columns)
    return {p.rsplit(".", 1)[-1].split("[")[0] for p, _ in flatten(step.result)}


def _field_scope(step, entry: IntentEntry, evidence: Evidence, session: Session) -> Verdict | None:
    if not entry.fields:
        return None
    observed = _observed_fields(step)
    if not observed:
        return None
    extra = sorted(observed - set(entry.fields))
    if extra:
        return Verdict(
            check_id=CHECK_FIELD, family="family3", status="violated", effect="flag",
            session_id=session.session_id, evidence=evidence,
            detail=f"{entry.intent}: field(s) {extra} exceed the approved set {list(entry.fields)}",
        )
    return Verdict(
        check_id=CHECK_FIELD, family="family3", status="satisfied", effect="allow",
        session_id=session.session_id, evidence=evidence,
        detail=f"{entry.intent}: returned fields within the approved set",
    )


def _result_rows(result) -> list:
    """Unwrap the {"columns": [...], "rows": [...], "row_count": N} shape a
    list-returning tool call carries; a single-row/scalar result is already
    the one row to check."""
    if isinstance(result, dict) and isinstance(result.get("rows"), list):
        return result["rows"]
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return [result]
    return []


def _filter_scope(step, entry: IntentEntry, evidence: Evidence, session: Session) -> Verdict | None:
    for mf in entry.mandatory_filters:
        m = _CALLER_FILTER.match(mf)
        if not m:
            continue
        field_name = m.group(1)
        rows = _result_rows(step.result)
        values = [r.get(field_name) for r in rows if isinstance(r, dict) and field_name in r]
        if not values:
            continue
        offending = [v for v in values if str(v) != str(session.user_id)]
        if offending:
            return Verdict(
                check_id=CHECK_FILTER, family="family3", status="violated", effect="block",
                session_id=session.session_id, evidence=evidence,
                detail=f"{entry.intent}: '{field_name}' scoped to caller failed for {len(offending)} row(s)",
            )
        return Verdict(
            check_id=CHECK_FILTER, family="family3", status="satisfied", effect="allow",
            session_id=session.session_id, evidence=evidence,
            detail=f"{entry.intent}: every row's '{field_name}' matches the caller",
        )
    return None


def _volume_scope(step, entry: IntentEntry, evidence: Evidence, session: Session) -> Verdict | None:
    if entry.expected_rows_p99 is None or step.row_count is None:
        return None
    ceiling = max(entry.expected_rows_p99 * VOLUME_MULTIPLE, entry.expected_rows_p99 + VOLUME_ABS_SLACK)
    if step.row_count > ceiling:
        return Verdict(
            check_id=CHECK_VOLUME, family="family3", status="violated", effect="flag",
            session_id=session.session_id, evidence=evidence,
            detail=f"{entry.intent}: {step.row_count} rows far exceeds expected p99={entry.expected_rows_p99}",
        )
    return Verdict(
        check_id=CHECK_VOLUME, family="family3", status="satisfied", effect="allow",
        session_id=session.session_id, evidence=evidence,
        detail=f"{entry.intent}: {step.row_count} rows within expected magnitude",
    )


def evaluate(session: Session, catalog: IntentCatalog, ctx: CheckContext) -> list[Verdict]:
    out: list[Verdict] = []
    for step in session.steps:
        entry = catalog.intents.get(step.intent) if step.intent else None
        if entry is None:
            continue
        evidence = Evidence(span_ids=(step.span_id,), excerpt=step.tool_name)
        for verdict in (
            _field_scope(step, entry, evidence, session),
            _filter_scope(step, entry, evidence, session),
            _volume_scope(step, entry, evidence, session),
        ):
            if verdict is not None:
                out.append(verdict)
    return out
