"""The verdict/finding contract every check, family, and mode shares.

Frozen so a check cannot accidentally mutate a verdict after emitting it
(Hard Rule 3: checks are pure). Version fields are stamped by the combinator,
never by the checks themselves - a check does not know which artifact
versions are in play (Hard Rule 11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Status = Literal["satisfied", "violated", "indeterminate"]
Effect = Literal["block", "approval_required", "flag", "allow"]
Family = Literal["family1", "family2", "family3"]
Mode = Literal["inline", "oob"]
IndeterminateReason = Literal["missing_precondition", "visibility_gap"]

# block > approval_required > flag > allow. The ONLY place this ordering is
# defined (Hard Rule 5) - combinator.py is the only module allowed to read it.
EFFECT_PRECEDENCE: dict[Effect, int] = {"block": 3, "approval_required": 2, "flag": 1, "allow": 0}


@dataclass(frozen=True)
class Evidence:
    """Span-id references + a minimal excerpt - never a full payload copy (Hard Rule 8)."""

    span_ids: tuple[str, ...] = ()
    excerpt: str = ""


@dataclass(frozen=True)
class Verdict:
    """What a single check emits for a single applicability match.

    A check emits nothing at all for a unit (step/param/session) it does not
    consider applicable - absence IS the "not applicable" signal (Hard Rule 16).
    When it does apply, it emits exactly one of satisfied/violated/indeterminate
    (Hard Rule 15: satisfied is first-class, never dropped).
    """

    check_id: str
    family: Family
    status: Status
    effect: Effect
    session_id: str
    evidence: Evidence
    rule_id: str = ""
    detail: str = ""
    # Set only when status == "indeterminate": names the visibility_profile
    # capture key this indeterminacy hinges on, so the combinator can split it
    # into missing_precondition vs visibility_gap (Hard Rule 7). Left blank if
    # the indeterminacy has nothing to do with capture coverage.
    missing_capture: str = ""
    # Copied verbatim from the rule pack's source block (Hard Rule 17). Never
    # populated by family2 checks; family3 only when the intent catalog entry
    # declares one.
    source: Optional[dict] = None


@dataclass(frozen=True)
class VersionStamp:
    engine_version: str = ""
    binding_profile_version: str = ""
    visibility_profile_version: str = ""
    rule_pack_version: str = ""
    catalog_version: str = ""


@dataclass(frozen=True)
class Finding:
    """A verdict, version-stamped and mode-resolved - what gets persisted/served."""

    verdict: Verdict
    versions: VersionStamp
    mode: Mode
    indeterminate_reason: Optional[IndeterminateReason] = None
    evaluated_at: str = ""  # ISO timestamp, bookkeeping only - never part of dedup identity
    # A fresh uuid4 per Finding, assigned by the combinator (combine_oob) at
    # persistence time - never by the check that emitted the Verdict (checks
    # stay pure/deterministic, same reason version stamps are stamped here
    # and not by the check). NOT part of the ClickHouse dedup identity
    # (`ORDER BY (session_id, check_id, rule_id, evidence_excerpt)`, ch.py) -
    # two persisted rows for the "same" logical finding still collapse to one
    # via ReplacingMergeTree; event_id just names THIS row, the one that won.
    # A stable API/UI key (React list key, deep-link, "copy finding id") that
    # doesn't require composing one from several fields.
    event_id: str = ""


@dataclass(frozen=True)
class ConformanceTag:
    """The positive half: one row per (session, check/rule) satisfied-and-exercised."""

    session_id: str
    check_id: str
    rule_id: str
    evidence: Evidence
    versions: VersionStamp
    source: Optional[dict] = None
    evaluated_at: str = ""


# --- reconstructed trace shape (evalengine.reconstruct builds these) ----------


@dataclass(frozen=True)
class Step:
    """One tool call, in canonical shape - independent of the subject app's span vocabulary."""

    span_id: str
    trace_id: str
    seq: int
    start_time: str
    end_time: str
    tool_name: str
    intent: str
    args: dict
    result: object
    status: str  # OK | ERROR | UNSET
    row_count: Optional[int] = None
    columns: tuple[str, ...] = ()
    side_effect: str = ""
    trust_class: str = ""
    turn_seq: Optional[int] = None


@dataclass(frozen=True)
class Turn:
    span_id: str
    seq: int
    start_time: str
    end_time: str
    user_message: str
    assistant_message: str


@dataclass(frozen=True)
class Session:
    session_id: str
    trace_ids: tuple[str, ...]
    user_id: str
    caller_role: str
    channel: str
    turns: tuple[Turn, ...]
    steps: tuple[Step, ...]
    final_answer: str
    raw_span_count: int = 0


@dataclass
class CheckContext:
    """Everything a check needs besides the Session - bundled so check
    signatures stay uniform: (session, ctx) -> list[Verdict]."""

    binding_version: str
    visibility_profile: object  # evalengine.visibility.VisibilityProfile
    provenance: object  # evalengine.provenance.ProvenanceGraph
    config: dict = field(default_factory=dict)
