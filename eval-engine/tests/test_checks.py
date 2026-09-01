"""The check registry and the enable/disable artifact (evalengine.checks).

Two things are worth pinning down here. The registry must stay exactly the
set of ids the families really emit - a check absent from it could never be
disabled, and an id in it that no check emits would render a dead toggle in
the settings UI. And the settings object has to gate evaluation without any
check knowing it exists.
"""

from __future__ import annotations

import pytest

from evalengine import checks
from evalengine.binding import load as load_binding
from evalengine.checks import CheckSettings
from evalengine.contract import Evidence, Verdict
from evalengine.evaluate import evaluate_session, version_key
from evalengine.family1 import compilepack
from evalengine.family1.compilepack import RulePack
from evalengine.family2 import CHECKS as F2_CHECKS
from evalengine.family3 import call, population, scope, session as session_mod
from evalengine.family3.catalog import IntentCatalog
from evalengine.visibility import VisibilityProfile

from .helpers import DEFAULT_VISIBILITY, make_session, make_step, make_turn

# The real ids, gathered from the modules that emit them - the same
# derivation tests/test_compliance.py uses, deliberately repeated rather
# than imported, so the registry is checked against the CODE and not against
# another table that could be wrong in the same way.
F1_IDS = set(compilepack._DEFAULT_CHECK.values()) | {"substitution", "sequencing", "approval_gate"}
F2_IDS = {m.CHECK_ID for m in F2_CHECKS}
F3_IDS = {
    call.CHECK_MEMBERSHIP, call.CHECK_ENTITLEMENT, call.CHECK_VERSION, call.CHECK_SIDE_EFFECT,
    scope.CHECK_FIELD, scope.CHECK_FILTER, scope.CHECK_VOLUME,
    session_mod.CHECK_TOXIC, session_mod.CHECK_GOAL, session_mod.CHECK_WORKFLOW,
    session_mod.CHECK_REDUNDANCY,
    population.CHECK_OUTCOME, population.CHECK_DRIFT, population.CHECK_TREND,
}
ALL_IDS = F1_IDS | F2_IDS | F3_IDS


# --- the registry ------------------------------------------------------------

def test_registry_is_exactly_the_checks_the_families_emit():
    assert checks.ALL_IDS == ALL_IDS


def test_registry_families_match_the_emitting_modules():
    assert {c.check_id for c in checks.by_family("family1")} == F1_IDS
    assert {c.check_id for c in checks.by_family("family2")} == F2_IDS
    assert {c.check_id for c in checks.by_family("family3")} == F3_IDS


def test_every_check_has_a_title_and_a_one_line_detail():
    for c in checks.REGISTRY:
        assert c.title and not c.title.endswith("."), c.check_id
        assert c.detail.endswith("."), c.check_id
        assert "\n" not in c.detail, c.check_id


def test_registry_has_no_duplicate_ids():
    assert len(checks.BY_ID) == len(checks.REGISTRY)


def test_population_flag_marks_exactly_the_on_demand_checks():
    # These three run via evaluate_population, not during a session's own
    # evaluation - the distinction the settings UI surfaces.
    assert {c.check_id for c in checks.REGISTRY if c.population} == {
        population.CHECK_OUTCOME, population.CHECK_DRIFT, population.CHECK_TREND,
    }


def test_compliance_family_map_is_the_registry():
    from evalengine.compliance.classes import FAMILY_OF_CHECK

    assert FAMILY_OF_CHECK == checks.FAMILY_OF_CHECK


# --- CheckSettings -----------------------------------------------------------

def test_default_settings_enable_everything():
    s = CheckSettings()
    assert s.version == checks.DEFAULT_VERSION
    assert all(s.enabled(c.check_id) for c in checks.REGISTRY)


def test_version_is_stable_and_order_independent():
    a = CheckSettings.from_ids(["minimization", "redundancy"])
    b = CheckSettings.from_ids(["redundancy", "minimization"])
    assert a.version == b.version
    assert a.version != CheckSettings.from_ids(["minimization"]).version
    assert a.version != checks.DEFAULT_VERSION


def test_unknown_ids_are_dropped_not_rejected():
    s = CheckSettings.from_ids(["minimization", "no_such_check"])
    assert s.disabled == {"minimization"}


def test_json_round_trip_and_garbage_degrades_to_all_enabled():
    s = CheckSettings.from_ids(["param_taint", "goal_alignment"])
    assert CheckSettings.from_json(s.to_json()) == s
    for bad in ("", "not json", "[1,2", '{"disabled": "nope"}', "null"):
        assert CheckSettings.from_json(bad) == CheckSettings()


def _verdict(check_id: str) -> Verdict:
    return Verdict(check_id=check_id, family="family2", status="violated", effect="flag",
                   session_id="s", evidence=Evidence())


def test_keep_drops_only_disabled_checks():
    vs = [_verdict("minimization"), _verdict("param_taint")]
    assert [v.check_id for v in CheckSettings().keep(vs)] == ["minimization", "param_taint"]
    kept = CheckSettings.from_ids(["minimization"]).keep(vs)
    assert [v.check_id for v in kept] == ["param_taint"]


def test_describe_groups_by_family_and_reports_configuration():
    d = checks.describe(CheckSettings.from_ids(["minimization"]),
                        {"family1": False, "family2": True, "family3": False})
    assert [f["family"] for f in d["families"]] == ["family1", "family2", "family3"]
    assert [f["label"] for f in d["families"]] == ["Policy", "Integrity", "Conformance"]
    assert [f["configured"] for f in d["families"]] == [False, True, False]
    assert d["total"] == len(checks.REGISTRY)
    assert d["enabled"] == len(checks.REGISTRY) - 1
    assert d["disabled"] == ["minimization"]
    f2 = next(f for f in d["families"] if f["family"] == "family2")
    assert {c["check_id"]: c["enabled"] for c in f2["checks"]}["minimization"] is False


# --- the gate, end to end through evaluate_session ---------------------------

BINDING = load_binding()  # the bundled default profile, as the service uses it


def _session_with_a_family2_violation():
    # An argument no user message or prior result supplies: param_provenance
    # reports it as fabricated. Chosen because it needs no artifacts.
    turn = make_turn(0, user_message="check the record")
    step = make_step(0, "do_thing", args={"amount": 4242}, turn_seq=0)
    return make_session(steps=[step], turns=[turn])


def _findings(settings):
    session = _session_with_a_family2_violation()
    # evaluate_session reconstructs from spans, so drive the pieces it needs
    # directly by monkeypatching is not worth it here - call the family the
    # same way the pipeline does and apply the same gate.
    from evalengine.family2 import evaluate_all
    from .helpers import make_ctx

    return settings.keep(evaluate_all(session, make_ctx(session)))


def test_disabling_a_check_removes_its_verdicts_from_the_pipeline_output():
    all_on = {v.check_id for v in _findings(CheckSettings())}
    assert "param_provenance" in all_on
    off = {v.check_id for v in _findings(CheckSettings.from_ids(["param_provenance"]))}
    assert "param_provenance" not in off
    assert off == all_on - {"param_provenance"}


def test_version_key_changes_with_the_disabled_set():
    args = (BINDING, VisibilityProfile(version="1", captures={}), RulePack(), IntentCatalog())
    base = version_key(*args, CheckSettings())
    assert base.endswith(f"checks@{checks.DEFAULT_VERSION}")
    changed = version_key(*args, CheckSettings.from_ids(["minimization"]))
    assert changed != base
    # ...and is stable for the same set, so replay stays a no-op (Hard Rule 12)
    assert changed == version_key(*args, CheckSettings.from_ids(["minimization"]))


def test_evaluate_session_honours_the_settings_argument():
    # The full pipeline path, spans in - reconstruct needs raw rows, so this
    # goes through the same entry point worker.py and POST /eval/run use.
    spans = [
        {"span_id": "t0", "parent_span_id": "", "trace_id": "tr", "name": "turn 0",
         "start_time": "2026-01-01T00:00:00Z", "end_time": "2026-01-01T00:00:05Z",
         "session_id": "s1", "user_id": "u", "user_role": "r", "channel": "c",
         "input_value": '{"content": "check the record"}', "output_value": '{"content": "done"}',
         "attributes": {}, "kind": "LLM", "status": "OK", "intent_name": "", "tool_name": ""},
        {"span_id": "s0", "parent_span_id": "t0", "trace_id": "tr", "name": "tool do_thing",
         "start_time": "2026-01-01T00:00:01Z", "end_time": "2026-01-01T00:00:02Z",
         "session_id": "s1", "user_id": "u", "user_role": "r", "channel": "c",
         "input_value": '{"amount": 4242}', "output_value": '{"ok": true}',
         "attributes": {}, "kind": "TOOL", "status": "OK", "intent_name": "do_thing",
         "tool_name": "do_thing"},
    ]
    args = (BINDING, DEFAULT_VISIBILITY, RulePack(), IntentCatalog(), spans)
    on = {f.verdict.check_id for f in evaluate_session("s1", *args, CheckSettings())}
    assert on, "expected at least one verdict with everything enabled"
    one = sorted(on)[0]
    off = {f.verdict.check_id for f in evaluate_session("s1", *args, CheckSettings.from_ids([one]))}
    assert one not in off
    assert off == on - {one}


def test_disabling_every_check_yields_no_findings():
    args = (BINDING, DEFAULT_VISIBILITY, RulePack(), IntentCatalog(), [])
    none = CheckSettings.from_ids([c.check_id for c in checks.REGISTRY])
    assert none.keep([_verdict("minimization")]) == []
    assert evaluate_session("s1", *args, none) == []


def test_module_holder_defaults_to_everything_enabled():
    assert checks.current() == checks.EMPTY
