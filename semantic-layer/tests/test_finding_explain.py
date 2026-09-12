"""Plain-language finding explanations (finding_explain.py).

The model's prose is advisory; what these pin is what it is GIVEN, that bad
output is refused rather than half-used, and that the cache is keyed so a
finding is paid for once and a changed finding is never served a stale one.
"""

from __future__ import annotations

import json

import pytest

from semanticlayer.finding_explain import EXPLAIN_SYSTEM, FindingIn, cache_key, explain, render
from semanticlayer.store import Store


def finding(**over):
    base = {
        "check_id": "field_restriction", "rule_id": "R-X", "family_label": "Policy",
        "status": "violated", "effect": "block",
        "detail": "rule R-X: restricted field(s) ['secret_score (final answer)'] surfaced on turn 0's answer",
        "evidence_excerpt": "turn 0: R-X",
        "source": json.dumps({"document": "policy.md", "section": "8.6 Confidentiality",
                              "text": "This score must never be disclosed to any user."}),
        "user_query": "Is application 7 ready to approve?",
    }
    base.update(over)
    return FindingIn.model_validate(base)


class _LLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.prompts.append((system, user))
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


def test_the_model_is_given_the_policy_text_and_the_question():
    """Without the cited text it can only paraphrase the jargon; without the
    question it cannot say what happened in this conversation."""
    p = render(finding())
    assert "technical wording: rule R-X" in p
    assert "policy cited: 8.6 Confidentiality: This score must never be disclosed" in p
    assert "the user asked the agent: Is application 7 ready to approve?" in p


def test_a_finding_with_no_citation_still_renders():
    p = render(finding(source="", user_query=""))
    assert "policy cited" not in p and "user asked" not in p


def test_the_prompt_forbids_ids_and_invented_facts():
    assert "No rule ids" in EXPLAIN_SYSTEM and "ONLY facts in the input" in EXPLAIN_SYSTEM


def test_explain_parses_the_two_fields():
    ex = explain(finding(), _LLM({"headline": "The agent revealed a confidential score",
                                  "explanation": "It did. Policy forbids it."}))
    assert ex.headline.startswith("The agent revealed")


@pytest.mark.parametrize("payload", ["not json", {"headline": "only one"}, {"headline": " ", "explanation": "x"}])
def test_bad_model_output_is_refused_not_half_used(payload):
    with pytest.raises(Exception):
        explain(finding(), _LLM(payload))


def test_cache_key_is_stable_and_changes_with_content_or_model():
    a = cache_key(finding(), "m")
    assert a == cache_key(finding(), "m")
    assert a != cache_key(finding(detail="something else"), "m")
    assert a != cache_key(finding(), "other-model")


def _row(event_id, detail="d1", status="violated", app_id="a"):
    return {"event_id": event_id, "app_id": app_id, "status": status, "detail": detail,
            "check_id": "c", "rule_id": "r", "source": "", "user_query": "q"}


def _worker(tmp_path, rows, payload=None, per_poll=25):
    from semanticlayer.finding_explain import ExplainWorker
    llm = _LLM(payload or {"headline": "H", "explanation": "E."})
    w = ExplainWorker(Store(tmp_path / "w.db"), lambda: llm, model="m",
                      fetch=lambda since, limit, offset: rows[offset:offset + limit],
                      per_poll=per_poll, page_size=2)
    return w, llm


def test_worker_explains_each_distinct_finding_once_and_links_every_event(tmp_path):
    """Identical content shares one summary; every event still finds it."""
    rows = [_row("1"), _row("2"), _row("3", detail="d2"), _row("4", status="satisfied")]
    w, llm = _worker(tmp_path, rows)
    assert w.poll_once() == 2 and len(llm.prompts) == 2
    got = w.store.explanations_for_app("a")
    assert set(got) == {"1", "2", "3"} and got["1"]["headline"] == "H"
    assert w.poll_once() == 0      # everything cached: no further calls


def test_worker_works_a_backlog_through_its_budget(tmp_path):
    """Over budget, it stays on the long window so the rest are not skipped."""
    rows = [_row(str(i), detail=f"d{i}") for i in range(5)]
    w, llm = _worker(tmp_path, rows, per_poll=2)
    assert w.poll_once() == 2 and w.backfilling
    assert w.poll_once() == 2 and w.poll_once() == 1 and not w.backfilling
    assert len(w.store.explanations_for_app("a")) == 5


def test_worker_gives_up_on_a_finding_after_three_failures(tmp_path):
    w, llm = _worker(tmp_path, [_row("1")], payload="not json")
    for _ in range(4):
        w.poll_once()
    assert len(llm.prompts) == 3 and w.store.explanations_for_app("a") == {}


def test_worker_reads_indeterminate_verdicts_too(tmp_path):
    """/eval/findings is violations only; an indeterminate verdict is also
    something a reader has to act on, and comes from a second feed."""
    from semanticlayer.finding_explain import ExplainWorker
    llm = _LLM({"headline": "H", "explanation": "E."})
    viol = [_row("1")]
    indet = [{**_row("2", detail="d2", status="indeterminate"), "indeterminate_reason": "no symbol x"}]
    feed = lambda rows: (lambda since, limit, offset: rows[offset:offset + limit])
    w = ExplainWorker(Store(tmp_path / "w.db"), lambda: llm, model="m", feeds=[feed(viol), feed(indet)])
    assert w.poll_once() == 2
    assert set(w.store.explanations_for_app("a")) == {"1", "2"}
    assert "why the check could not tell: no symbol x" in llm.prompts[1][1]


def test_the_indeterminate_reason_only_changes_the_key_when_set():
    assert cache_key(finding(), "m") == cache_key(finding(indeterminate_reason=""), "m")
    assert cache_key(finding(indeterminate_reason="why"), "m") != cache_key(finding(), "m")


def test_a_prompt_change_is_a_new_cache_entry(monkeypatch):
    """Otherwise a better prompt only ever reaches findings not yet cached."""
    import semanticlayer.finding_explain as fe
    before = fe.cache_key(finding(), "m")
    monkeypatch.setattr(fe, "PROMPT_VERSION", fe.PROMPT_VERSION + 1)
    assert fe.cache_key(finding(), "m") != before


def test_event_and_app_ids_are_not_part_of_the_cache_key():
    assert cache_key(finding(event_id="1", app_id="x"), "m") == cache_key(finding(event_id="2"), "m")


def test_explanations_round_trip_through_the_store(tmp_path):
    s = Store(tmp_path / "t.db")
    assert s.get_explanation("k") is None
    s.put_explanation("k", "Headline", "Two sentences.", "m")
    assert s.get_explanation("k") == {"headline": "Headline", "explanation": "Two sentences.", "model": "m"}
