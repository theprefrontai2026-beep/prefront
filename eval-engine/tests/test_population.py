from evalengine.family3.population import invocation_drift, outcome_consistency, verdict_trend


def _shape_row(session_id, variant, shape):
    return {"session_id": session_id, "variant": variant, "shape": shape}


def test_outcome_consistency_stable_shape_is_satisfied():
    rows = [_shape_row("s1", "v2", "a,b"), _shape_row("s2", "v2", "a,b"), _shape_row("s3", "v2", "a,b")]
    v = outcome_consistency("POP-01", rows)
    assert v.status == "satisfied"


def test_outcome_consistency_varying_shape_is_violated():
    rows = [_shape_row("s1", "v2", "a,b"), _shape_row("s2", "v2", "a,b,c"), _shape_row("s3", "v2", "a")]
    v = outcome_consistency("POP-01", rows)
    assert v.status == "violated"
    assert "3 distinct action shapes" in v.detail


def test_outcome_consistency_not_enough_sessions_is_none():
    rows = [_shape_row("s1", "v2", "a,b")]
    assert outcome_consistency("POP-01", rows) is None


def test_invocation_drift_stable_mix_is_satisfied():
    v1 = [_shape_row(f"v1-{i}", "v1", "a,b") for i in range(3)]
    v2 = [_shape_row(f"v2-{i}", "v2", "a,b") for i in range(3)]
    v = invocation_drift("POP-02", v1 + v2, "v1", "v2")
    assert v.status == "satisfied"


def test_invocation_drift_shifted_mix_is_violated():
    v1 = [_shape_row(f"v1-{i}", "v1", "a") for i in range(3)]
    v2 = [_shape_row(f"v2-{i}", "v2", "a,b,c") for i in range(3)]
    v = invocation_drift("POP-02", v1 + v2, "v1", "v2")
    assert v.status == "violated"


def test_invocation_drift_missing_variant_is_none():
    v1 = [_shape_row(f"v1-{i}", "v1", "a") for i in range(3)]
    assert invocation_drift("POP-02", v1, "v1", "v2") is None


def test_verdict_trend_persistent_violation():
    rows = [{"session_id": f"s{i}", "status": "violated", "evaluated_at": "t"} for i in range(4)]
    v = verdict_trend("R-APPROVAL-OVER-50K", rows)
    assert v.status == "violated"
    assert "100%" in v.detail


def test_verdict_trend_mostly_clean_is_satisfied():
    rows = (
        [{"session_id": "s1", "status": "satisfied", "evaluated_at": "t"}]
        + [{"session_id": "s2", "status": "satisfied", "evaluated_at": "t"}]
        + [{"session_id": "s3", "status": "violated", "evaluated_at": "t"}]
    )
    v = verdict_trend("R-APPROVAL-OVER-50K", rows)
    assert v.status == "satisfied"


def test_verdict_trend_dedupes_by_session_newest_first():
    # s1 re-evaluated (newest row first) flipped from violated to satisfied -
    # only the newest status per session should count.
    rows = [
        {"session_id": "s1", "status": "satisfied", "evaluated_at": "t2"},
        {"session_id": "s1", "status": "violated", "evaluated_at": "t1"},
        {"session_id": "s2", "status": "satisfied", "evaluated_at": "t1"},
    ]
    v = verdict_trend("R-X", rows)
    assert v.status == "satisfied"
