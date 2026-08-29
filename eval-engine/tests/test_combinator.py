from evalengine.combinator import combine_inline, combine_oob, conformance_tags, violations
from evalengine.contract import Evidence, VersionStamp, Verdict
from evalengine.visibility import VisibilityProfile


def _v(check_id, status, effect, missing_capture="", rule_id=""):
    return Verdict(
        check_id=check_id, family="family2", status=status, effect=effect,
        session_id="sess-1", evidence=Evidence(span_ids=("s1",), excerpt="x"),
        missing_capture=missing_capture, rule_id=rule_id,
    )


def test_combine_inline_precedence_block_wins():
    effect, _ = combine_inline([
        _v("a", "satisfied", "allow"),
        _v("b", "violated", "flag"),
        _v("c", "violated", "block"),
        _v("d", "violated", "approval_required"),
    ])
    assert effect == "block"


def test_combine_inline_indeterminate_never_allows():
    effect, _ = combine_inline([_v("a", "indeterminate", "allow")])
    assert effect == "approval_required"


def test_combine_inline_empty_is_allow():
    assert combine_inline([]) == ("allow", [])


def test_combine_oob_resolves_missing_precondition_when_captured():
    visibility = VisibilityProfile(version="1", captures={"approval_events": True})
    verdicts = [_v("approval_evidence", "indeterminate", "approval_required", missing_capture="approval_events")]
    findings = combine_oob(verdicts, visibility, VersionStamp(), "2026-01-01T00:00:00Z")
    assert findings[0].indeterminate_reason == "missing_precondition"


def test_combine_oob_resolves_visibility_gap_when_not_captured():
    visibility = VisibilityProfile(version="1", captures={"approval_events": False})
    verdicts = [_v("approval_evidence", "indeterminate", "approval_required", missing_capture="approval_events")]
    findings = combine_oob(verdicts, visibility, VersionStamp(), "2026-01-01T00:00:00Z")
    assert findings[0].indeterminate_reason == "visibility_gap"


def test_combine_oob_never_drops_a_verdict():
    visibility = VisibilityProfile(version="1", captures={})
    verdicts = [_v("a", "satisfied", "allow"), _v("b", "violated", "block"), _v("c", "indeterminate", "flag")]
    findings = combine_oob(verdicts, visibility, VersionStamp(), "2026-01-01T00:00:00Z")
    assert len(findings) == 3


def test_violations_and_conformance_tags_partition_by_status():
    visibility = VisibilityProfile(version="1", captures={})
    verdicts = [_v("a", "satisfied", "allow"), _v("b", "violated", "block"), _v("c", "indeterminate", "flag")]
    findings = combine_oob(verdicts, visibility, VersionStamp(), "2026-01-01T00:00:00Z")
    assert len(violations(findings)) == 1
    assert len(conformance_tags(findings)) == 1


def test_combine_oob_leaves_event_id_unassigned():
    # event_id is a monotonic serial number assigned at PERSIST time
    # (ch.insert_verdicts, see test_ch_event_id.py) - the combinator has no
    # counter to hand one out from and shouldn't (Hard Rule 3: checks and
    # the combinator stay pure).
    visibility = VisibilityProfile(version="1", captures={})
    verdicts = [_v("a", "satisfied", "allow"), _v("b", "violated", "block")]
    findings = combine_oob(verdicts, visibility, VersionStamp(), "2026-01-01T00:00:00Z")
    assert all(f.event_id == "" for f in findings)
