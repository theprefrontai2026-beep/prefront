"""governance/trace.py - the hash-chained audit log (compliance_design.md §5.4).
Pure filesystem, no DB."""

from __future__ import annotations

import json

import pytest

from semanticmcp.governance import trace


@pytest.fixture(autouse=True)
def _fresh_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_PATH", str(tmp_path / "traces.jsonl"))
    with trace._lock:
        trace._state.update(path=None, last_hash=None, written=0, failures=0, last_error="")
    yield


def _rec(i: int) -> dict:
    return {"trace_id": f"t{i}", "tool": "x", "decision": "allowed", "parameters": {"n": i}}


def test_lines_are_chained_and_verify(tmp_path):
    for i in range(3):
        assert trace.persist(_rec(i)) is True
    lines = [json.loads(l) for l in (tmp_path / "traces.jsonl").read_text().splitlines()]
    assert lines[0]["prev_hash"] == trace.GENESIS_HASH
    assert lines[1]["prev_hash"] == lines[0]["hash"] and lines[2]["prev_hash"] == lines[1]["hash"]
    v = trace.verify_chain()
    assert v["ok"] and v["checked"] == 3
    assert trace.audit_status()["written"] == 3 and trace.audit_status()["failures"] == 0


def test_the_returned_record_carries_the_link():
    rec = _rec(0)
    trace.persist(rec)
    assert rec["prev_hash"] == trace.GENESIS_HASH and len(rec["hash"]) == 64


def test_tampering_breaks_the_chain(tmp_path):
    for i in range(3):
        trace.persist(_rec(i))
    p = tmp_path / "traces.jsonl"
    lines = p.read_text().splitlines()
    edited = json.loads(lines[1])
    edited["decision"] = "blocked"
    lines[1] = json.dumps(edited)
    p.write_text("\n".join(lines) + "\n")
    v = trace.verify_chain()
    assert not v["ok"] and v["first_bad_line"] == 2 and v["error"] == "hash mismatch"


def test_deleting_a_line_breaks_the_chain(tmp_path):
    for i in range(3):
        trace.persist(_rec(i))
    p = tmp_path / "traces.jsonl"
    lines = p.read_text().splitlines()
    del lines[1]
    p.write_text("\n".join(lines) + "\n")
    v = trace.verify_chain()
    assert not v["ok"] and v["first_bad_line"] == 2


def test_restart_continues_the_chain_from_the_file(tmp_path):
    trace.persist(_rec(0))
    last = trace.audit_status()
    # simulate a process restart: forget in-memory state, keep the file
    with trace._lock:
        trace._state.update(path=None, last_hash=None)
    trace.persist(_rec(1))
    assert trace.verify_chain()["ok"] and trace.verify_chain()["checked"] == 2
    assert last["written"] == 1


def test_legacy_unchained_prefix_is_tolerated_but_a_gap_is_not(tmp_path):
    p = tmp_path / "traces.jsonl"
    p.write_text(json.dumps({"trace_id": "old", "decision": "allowed"}) + "\n")
    trace.persist(_rec(1))
    assert trace.verify_chain()["ok"]
    # now an unchained line AFTER a chained one
    with p.open("a") as f:
        f.write(json.dumps({"trace_id": "sneaky"}) + "\n")
    v = trace.verify_chain()
    assert not v["ok"] and v["error"].startswith("unchained")


def test_write_failure_is_counted_never_raised(tmp_path, monkeypatch):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("i am a file")
    monkeypatch.setenv("TRACE_PATH", str(blocker / "traces.jsonl"))
    rec = _rec(0)
    assert trace.persist(rec) is False
    s = trace.audit_status()
    assert s["failures"] == 1 and s["last_error"]
    assert "hash" not in rec  # never claim a link that was not written


def test_verify_missing_file():
    v = trace.verify_chain("/nonexistent/traces.jsonl")
    assert not v["ok"] and v["error"] == "file not found"
