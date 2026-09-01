"""Unit tests for ch.list_feed - the unified Decision Traces feed that surfaces
a session ALONGSIDE the policy/rule it was seen with, whatever the outcome.

Pure, no real ClickHouse: the underlying `list_verdicts` (the SELECT) and
`_first_user_messages` (the spans-table join) are monkeypatched, matching the
style of test_ch_event_id.py. What we assert is list_feed's contract, which is
exactly what the "show the relevant policy a session was seen with" feature
depends on:

  1. it returns EVERY status (satisfied included), unlike list_findings which
     hard-filters to violated;
  2. it preserves each row's `source` policy citation (section/document/clause);
  3. it joins the session's first user turn onto each row.
"""

from __future__ import annotations

import json

import pytest

from evalengine import ch
from evalengine import checks


@pytest.fixture(autouse=True)
def _no_clickhouse(monkeypatch):
    """Nothing in this module talks to a real ClickHouse. `list_feed` now also
    stamps `occurred_at` from the shared spans table (ch._occurred_at), so the
    row reads it does are stubbed to "no spans" here; the test that actually
    cares about that stamping patches `rows` itself."""
    monkeypatch.setattr(ch, "rows", lambda *a, **kw: [])


# A satisfied row carries its policy citation in `source` (Family 1/3), a
# violated row cites a different clause, and an integrity row has none - the
# three shapes the unified feed must carry through untouched.
_ROWS = [
    {
        "session_id": "sess_A", "check_id": "field_restriction", "family": "family1",
        "status": "satisfied", "effect": "allow", "detail": "no restricted field surfaced",
        "source": json.dumps({"document": "policy.md", "section": "12.1 SSN",
                              "text": "The SSN must never appear in a response."}),
    },
    {
        "session_id": "sess_A", "check_id": "entitlement", "family": "family3",
        "status": "violated", "effect": "block", "detail": "caller not entitled to view_risk_profile",
        "source": json.dumps({"document": "policy.md", "section": "8.6 Internal Risk Score"}),
    },
    {
        "session_id": "sess_B", "check_id": "param_provenance", "family": "family2",
        "status": "satisfied", "effect": "allow", "detail": "all args traceable",
        "source": "",
    },
]


def _fake_list_verdicts(**kwargs):
    # Emulate the SELECT: honour a status filter the way the real query does,
    # so we can prove list_feed does NOT impose one of its own.
    rows = _ROWS
    if kwargs.get("status"):
        rows = [r for r in rows if r["status"] == kwargs["status"]]
    return {"verdicts": [dict(r) for r in rows], "total": len(rows),
            "limit": kwargs.get("limit", 100), "offset": kwargs.get("offset", 0)}


def _fake_queries(session_ids):
    q = {"sess_A": "What's the status of application 7010?",
         "sess_B": "Show me my applications"}
    return {sid: q[sid] for sid in session_ids if sid in q}


def test_feed_returns_every_status(monkeypatch):
    monkeypatch.setattr(ch, "list_verdicts", _fake_list_verdicts)
    monkeypatch.setattr(ch, "_first_user_messages", _fake_queries)
    out = ch.list_feed(limit=500)
    statuses = sorted(v["status"] for v in out["verdicts"])
    assert statuses == ["satisfied", "satisfied", "violated"], \
        "the unified feed must include satisfied rows, not only violations"


def test_feed_preserves_policy_citation(monkeypatch):
    monkeypatch.setattr(ch, "list_verdicts", _fake_list_verdicts)
    monkeypatch.setattr(ch, "_first_user_messages", _fake_queries)
    out = ch.list_feed(limit=500)
    sat = next(v for v in out["verdicts"] if v["check_id"] == "field_restriction")
    src = json.loads(sat["source"])
    # This is the whole point: a CLEAN row is shown associated with the policy
    # section/clause it satisfied.
    assert sat["status"] == "satisfied"
    assert src["section"] == "12.1 SSN"
    assert "SSN must never appear" in src["text"]


def test_feed_joins_user_query(monkeypatch):
    monkeypatch.setattr(ch, "list_verdicts", _fake_list_verdicts)
    monkeypatch.setattr(ch, "_first_user_messages", _fake_queries)
    out = ch.list_feed(limit=500)
    for v in out["verdicts"]:
        assert v["user_query"], "every row should carry its session's first user turn"
    a = next(v for v in out["verdicts"] if v["session_id"] == "sess_A")
    assert a["user_query"] == "What's the status of application 7010?"


def test_findings_stays_violations_only(monkeypatch):
    """Contrast: list_findings must still hard-filter to violated, so the two
    reads stay distinct (the feed is the superset)."""
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return _fake_list_verdicts(**kwargs)

    monkeypatch.setattr(ch, "list_verdicts", capture)
    monkeypatch.setattr(ch, "_first_user_messages", _fake_queries)
    out = ch.list_findings(limit=500)
    assert seen.get("status") == "violated", "list_findings must request only violated rows"
    assert all(f["status"] == "violated" for f in out["findings"])


# ── include_disabled: seeing what a disabled check is holding back ──────────
# Disabling a check HIDES its records on every read; it never deletes them
# (ch._disabled_clause). The feed is the one read that can be asked for them
# anyway, so the UI can show them labelled instead of a reader having to
# re-enable the check to find out what is there.

def test_disabled_clause_filters_by_default_and_is_skippable(monkeypatch):
    monkeypatch.setattr(checks, "current", lambda: checks.CheckSettings.from_ids(["minimization"]))

    params: dict = {}
    assert ch._disabled_clause(params) == " AND check_id NOT IN %(disabled_checks)s"
    assert params["disabled_checks"] == ["minimization"]

    opted_in: dict = {}
    assert ch._disabled_clause(opted_in, include_disabled=True) == ""
    assert opted_in == {}, "opting in must not bind a filter parameter either"


def test_feed_passes_include_disabled_through_and_names_the_set(monkeypatch):
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return _fake_list_verdicts(**kwargs)

    monkeypatch.setattr(ch, "list_verdicts", capture)
    monkeypatch.setattr(ch, "_first_user_messages", _fake_queries)
    monkeypatch.setattr(checks, "current", lambda: checks.CheckSettings.from_ids(["minimization"]))

    out = ch.list_feed(limit=500, include_disabled=True)
    assert seen.get("include_disabled") is True
    # Named, so the caller can label those rows rather than mixing them in.
    assert out["disabled_checks"] == ["minimization"]


def test_feed_hides_disabled_by_default(monkeypatch):
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return _fake_list_verdicts(**kwargs)

    monkeypatch.setattr(ch, "list_verdicts", capture)
    monkeypatch.setattr(ch, "_first_user_messages", _fake_queries)
    monkeypatch.setattr(checks, "current", lambda: checks.CheckSettings.from_ids(["minimization"]))

    out = ch.list_feed(limit=500)
    assert seen.get("include_disabled") is False, "the hide rule stands unless asked"
    assert "disabled_checks" not in out


# ── occurred_at: when the activity happened, not when the engine ran ────────

def test_occurred_at_prefers_the_cited_span_then_the_session(monkeypatch):
    """`evaluated_at` is the evaluation pass's own clock - batched, so a whole
    catalogue shares one timestamp, and a re-evaluation moves all of them. The
    feed therefore carries the time the evaluated ACTIVITY happened: the cited
    evidence span's start, or the session's first span when a verdict cites no
    span (a session-level check)."""
    def fake_rows(sql, params=None):
        if "span_id IN" in sql:
            return [{"span_id": "span_1", "t": "2026-09-01T09:00:01+00:00"}]
        return [{"session_id": "sess_A", "t": "2026-09-01T08:59:00+00:00"},
                {"session_id": "sess_B", "t": "2026-09-01T08:30:00+00:00"}]

    monkeypatch.setattr(ch, "rows", fake_rows)
    items = [
        {"session_id": "sess_A", "evidence_span_ids": ["span_1"]},   # cites a span
        {"session_id": "sess_A", "evidence_span_ids": []},           # session-level
        {"session_id": "sess_B", "evidence_span_ids": ["gone"]},     # span aged out
        {"session_id": "sess_C", "evidence_span_ids": []},           # session aged out
    ]
    ch._occurred_at(items)
    assert items[0]["occurred_at"] == "2026-09-01T09:00:01+00:00", "the cited span wins"
    assert items[1]["occurred_at"] == "2026-09-01T08:59:00+00:00", "session start when no span is cited"
    assert items[2]["occurred_at"] == "2026-09-01T08:30:00+00:00", "falls back when the span is gone"
    # Never substitutes evaluated_at: an empty value says "not known", which a
    # caller can render differently from a real time.
    assert items[3]["occurred_at"] == ""


def test_feed_stamps_occurred_at(monkeypatch):
    monkeypatch.setattr(ch, "list_verdicts", _fake_list_verdicts)
    monkeypatch.setattr(ch, "_first_user_messages", _fake_queries)
    out = ch.list_feed(limit=500)
    assert all("occurred_at" in v for v in out["verdicts"])
