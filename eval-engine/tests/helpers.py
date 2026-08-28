"""Synthetic-Session builders for check unit tests (autonomous-build protocol
step 3: checks are pure functions, unit-testable without Docker)."""

from __future__ import annotations

from evalengine.contract import CheckContext, Session, Step, Turn
from evalengine.provenance import build as build_provenance
from evalengine.visibility import VisibilityProfile

DEFAULT_VISIBILITY = VisibilityProfile(version="1", captures={
    "tool_args": True, "tool_results": True, "llm_messages": True,
    "approval_events": False, "sql": False,
})


def make_step(seq, tool_name, args=None, result=None, status="OK", row_count=None,
             columns=(), side_effect="read", trust_class="", turn_seq=0, span_id=None, intent=None):
    return Step(
        span_id=span_id or f"step-{seq}",
        trace_id="trace-1",
        seq=seq,
        start_time=f"2026-01-01T00:00:{seq:02d}Z",
        end_time=f"2026-01-01T00:00:{seq:02d}Z",
        tool_name=tool_name,
        intent=tool_name if intent is None else intent,
        args=args or {},
        result=result,
        status=status,
        row_count=row_count,
        columns=tuple(columns),
        side_effect=side_effect,
        trust_class=trust_class,
        turn_seq=turn_seq,
    )


def make_turn(seq, user_message="", assistant_message="", span_id=None):
    return Turn(
        span_id=span_id or f"turn-{seq}",
        seq=seq,
        start_time=f"2026-01-01T00:0{seq}:00Z",
        end_time=f"2026-01-01T00:0{seq}:30Z",
        user_message=user_message,
        assistant_message=assistant_message,
    )


def make_session(steps=(), turns=(), session_id="sess-1", user_id="user-1",
                 caller_role="Applicant", channel="portal", final_answer=""):
    turns = tuple(turns)
    return Session(
        session_id=session_id,
        trace_ids=("trace-1",),
        user_id=user_id,
        caller_role=caller_role,
        channel=channel,
        turns=turns,
        steps=tuple(steps),
        final_answer=final_answer or (turns[-1].assistant_message if turns else ""),
        raw_span_count=len(steps) + len(turns),
    )


def make_ctx(session, visibility=DEFAULT_VISIBILITY, abs_tol=0.01, rel_tol=0.005, **config_overrides):
    prov = build_provenance(session, abs_tol, rel_tol)
    config = {
        "round_abs_tolerance": abs_tol,
        "round_rel_tolerance": rel_tol,
        "minimization_row_floor": 50,
        "minimization_multiple": 5.0,
    }
    config.update(config_overrides)
    return CheckContext(binding_version="1", visibility_profile=visibility, provenance=prov, config=config)
