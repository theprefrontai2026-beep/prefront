"""Unit tests for RuleExtractor.extract_clauses's on_progress callback -
exercises the REAL threaded/sequential extraction paths (a fake OpenAI-shaped
client is injected, not a stand-in for extract_clauses itself - see
test_api.py's extract-rules tests for the endpoint-level, fully-faked
version)."""

from __future__ import annotations

import threading
import time

from skillbuilder.llm import ExtractionContext, RuleExtractor
from skillbuilder.schema import Clause


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    def create(self, **_kwargs):
        time.sleep(self._delay)  # simulates real I/O latency
        return _FakeCompletion('{"candidate_rules": []}')


class _FakeChat:
    def __init__(self, delay: float) -> None:
        self.completions = _FakeCompletions(delay)


class _FakeClient:
    def __init__(self, delay: float = 0.02) -> None:
        self.chat = _FakeChat(delay)


def _clause(i: int) -> Clause:
    return Clause(
        clause_id=f"c{i}", document_id="d1", clause_type="restriction",
        source_text=f"clause {i} text",
    )


def _ctx() -> ExtractionContext:
    return ExtractionContext(domain="test")


def test_on_progress_reaches_total_and_stays_monotonic_threaded():
    extractor = RuleExtractor(client=_FakeClient(), max_workers=4)
    clauses = [_clause(i) for i in range(6)]
    seen: list[tuple[int, int]] = []
    lock = threading.Lock()

    def on_progress(completed, total):
        with lock:
            seen.append((completed, total))

    results = extractor.extract_clauses(clauses, _ctx(), on_progress=on_progress)

    assert len(results) == 6
    assert [r.clause.clause_id for r in results] == [f"c{i}" for i in range(6)], (
        "results must stay in INPUT order even though on_progress fires in "
        "completion order"
    )
    assert len(seen) == 6
    completions = [c for c, _ in seen]
    assert completions == sorted(completions), "completed count must be monotonic"
    assert completions[-1] == 6
    assert all(t == 6 for _, t in seen)


def test_on_progress_sequential_path_fires_in_order():
    extractor = RuleExtractor(client=_FakeClient(delay=0), max_workers=1)
    clauses = [_clause(i) for i in range(3)]
    seen: list[tuple[int, int]] = []

    results = extractor.extract_clauses(clauses, _ctx(), on_progress=lambda c, t: seen.append((c, t)))

    assert len(results) == 3
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_extract_clauses_without_on_progress_still_works():
    extractor = RuleExtractor(client=_FakeClient(delay=0), max_workers=4)
    clauses = [_clause(i) for i in range(4)]
    results = extractor.extract_clauses(clauses, _ctx())
    assert len(results) == 4
