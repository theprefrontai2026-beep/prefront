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

from evalengine import ch


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
