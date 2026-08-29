"""Unit tests for ch.py's serial event_id counter (_next_event_ids) - pure,
no real ClickHouse: `one()` (the seed query) is monkeypatched, matching how
Hard-Rule-9-adjacent per-process state is tested elsewhere in this repo
(e.g. semantic-mcp-server's session_state.py tests)."""

from __future__ import annotations

import threading

import pytest

from evalengine import ch


@pytest.fixture(autouse=True)
def _reset_seq():
    ch._event_seq = None
    yield
    ch._event_seq = None


def test_seeds_from_existing_max_then_increments(monkeypatch):
    monkeypatch.setattr(ch, "one", lambda *a, **kw: {"m": 41})
    ids = ch._next_event_ids(3)
    assert ids == ["42", "43", "44"]


def test_seeds_from_zero_when_table_empty(monkeypatch):
    monkeypatch.setattr(ch, "one", lambda *a, **kw: {"m": 0})
    assert ch._next_event_ids(2) == ["1", "2"]


def test_only_seeds_once(monkeypatch):
    calls = {"n": 0}

    def fake_one(*a, **kw):
        calls["n"] += 1
        return {"m": 10}

    monkeypatch.setattr(ch, "one", fake_one)
    assert ch._next_event_ids(1) == ["11"]
    assert ch._next_event_ids(1) == ["12"]
    assert calls["n"] == 1, "the seed query must run once per process, not once per call"


def test_concurrent_callers_never_collide(monkeypatch):
    monkeypatch.setattr(ch, "one", lambda *a, **kw: {"m": 0})
    seen: list[str] = []
    lock = threading.Lock()

    def worker():
        ids = ch._next_event_ids(5)
        with lock:
            seen.extend(ids)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 40
    assert len(set(seen)) == 40, "no two concurrent callers may be handed the same event_id"
