"""Unit tests for grading_harness.py's pure diff/grading logic - no network,
no Docker. Catches diff-logic bugs before spending a live (paid-LLM) run on
them. Run with: python3 -m pytest test_grading_harness.py -q
"""

from __future__ import annotations

from unittest.mock import patch

import grading_harness
from grading_harness import _cited, grade_scenario, render_report, wait_for_ingestion


def _verdict(check_id, status):
    return {"check_id": check_id, "status": status}


def _tag(section):
    return {"section": section}


def test_cited_matches_bare_number_inside_a_compound_section_string():
    assert _cited("13.5", ["9.3.8 Loan Amount / 13.5 Approval Before Decision"])
    assert not _cited("13.5", ["9.3.58 Something Else"])


def test_grade_scenario_all_expected_matched_is_pass():
    s = {"id": "X", "family": "F1", "expected_findings": [{"check": "prohibition"}]}
    verdicts = [_verdict("prohibition", "violated")]
    result = grade_scenario(s, verdicts, [])
    assert result["grade"] == "PASS"
    assert result["matched"] == ["prohibition"]
    assert result["missing"] == []


def test_grade_scenario_missing_check_is_fail():
    s = {"id": "X", "family": "F1", "expected_findings": [{"check": "prohibition"}]}
    result = grade_scenario(s, [], [])
    assert result["grade"] == "FAIL"
    assert result["missing"] == ["prohibition"]


def test_grade_scenario_indeterminate_match_is_partial():
    s = {"id": "X", "family": "F1", "mode": "llm",
        "expected_findings": [{"check": "approval_gate"}]}
    verdicts = [_verdict("approval_gate", "indeterminate")]
    result = grade_scenario(s, verdicts, [])
    assert result["grade"] == "PARTIAL"
    assert result["matched_indeterminate"] == ["approval_gate"]


def test_grade_scenario_no_expected_findings_is_pass_with_no_evidence():
    s = {"id": "X", "family": "F1", "expected_findings": []}
    assert grade_scenario(s, [], [])["grade"] == "PASS"


def test_grade_scenario_baseline_clean_is_pass():
    s = {"id": "BASE-01", "family": "BASE", "baseline": True, "expected_findings": [],
        "demonstrates": [{"policy": "13.2", "note": "n"}, {"policy": "13.3", "note": "n"}]}
    tags = [_tag("13.2 Verify Before Quoting"), _tag("13.3 Retrieve Risk Profile Before Pricing")]
    result = grade_scenario(s, [], tags)
    assert result["grade"] == "PASS"
    assert result["demonstrates_missing"] == []


def test_grade_scenario_baseline_missing_conformance_tag_is_fail():
    s = {"id": "BASE-01", "family": "BASE", "baseline": True, "expected_findings": [],
        "demonstrates": [{"policy": "13.2", "note": "n"}]}
    result = grade_scenario(s, [], [])
    assert result["grade"] == "FAIL"
    assert result["demonstrates_missing"] == ["13.2"]


def test_grade_scenario_baseline_unexpected_violation_is_fail():
    s = {"id": "BASE-01", "family": "BASE", "baseline": True, "expected_findings": [],
        "demonstrates": []}
    verdicts = [_verdict("field_scope", "violated")]
    result = grade_scenario(s, verdicts, [])
    assert result["grade"] == "FAIL"
    assert result["unexpected_violations"] == ["field_scope"]


def test_grade_scenario_extra_violation_does_not_fail_a_matched_scenario():
    s = {"id": "F2-01", "family": "F2", "mode": "replay",
        "expected_findings": [{"check": "param_provenance"}]}
    verdicts = [_verdict("param_provenance", "violated"), _verdict("error_blindness", "violated")]
    result = grade_scenario(s, verdicts, [])
    assert result["grade"] == "PASS"
    assert result["extra_violations"] == ["error_blindness"]


def test_render_report_counts_grades():
    results = [
        {"id": "A", "family": "F1", "mode": "llm", "grade": "PASS", "expected": ["prohibition"],
         "matched": ["prohibition"], "missing": [], "extra_violations": [], "baseline": False},
        {"id": "B", "family": "F1", "mode": "llm", "grade": "FAIL", "expected": ["prohibition"],
         "matched": [], "missing": ["prohibition"], "extra_violations": [], "baseline": False},
    ]
    report = render_report(results)
    assert "1/2 PASS, 0/2 PARTIAL, 1/2 FAIL" in report
    assert "| A | F1 | llm | **PASS**" in report
    assert "| B | F1 | llm | **FAIL**" in report


# --- wait_for_ingestion --------------------------------------------------------
# A real, reproducible flake this caught live: the old "spans truthy" check
# returned as soon as the FIRST span landed, letting evaluate_session() run
# against a partial trace and silently produce zero verdicts. Debounced
# instead - only "done" once the span count is non-zero and unchanged across
# two consecutive polls.

def test_wait_for_ingestion_waits_for_a_stable_nonzero_count(monkeypatch):
    monkeypatch.setattr(grading_harness, "INGEST_POLL_S", 0)
    responses = iter([{"spans": 0}, {"spans": 2}, {"spans": 5}, {"spans": 5}])
    with patch.object(grading_harness, "_get", lambda url: next(responses)):
        assert wait_for_ingestion("sess-1") is True


def test_wait_for_ingestion_never_stabilizing_times_out(monkeypatch):
    monkeypatch.setattr(grading_harness, "INGEST_POLL_S", 0)
    monkeypatch.setattr(grading_harness, "INGEST_TIMEOUT_S", 0)
    with patch.object(grading_harness, "_get", lambda url: {"spans": 1}):
        assert wait_for_ingestion("sess-1") is False
