from evalengine.family3 import call, scope
from evalengine.family3 import session as session_checks
from evalengine.family3.catalog import IntentCatalog, IntentEntry

from .helpers import make_ctx, make_session, make_step, make_turn


def _catalog(*entries):
    return IntentCatalog(version="1", intents={e.intent: e for e in entries})


VIEW_ACCOUNT = IntentEntry(
    intent="view_account", tool_name="get_account", params=("account_id",),
    side_effect="read", allowed_roles=("Teller",), allowed_channels=("branch",),
    fields=("account_id", "balance"), expected_rows_p99=1,
    mandatory_filters=["owner_id = caller"],
)


def _step_with_intent(seq, tool_name, intent, **kw):
    return make_step(seq, tool_name, turn_seq=0, intent=intent, **kw)


# --- call level -----------------------------------------------------------------

def test_catalog_membership_off_catalog_is_violated():
    step = _step_with_intent(0, "unknown_tool", "")
    session = make_session(steps=[step])
    verdicts = call.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert len(verdicts) == 1
    assert verdicts[0].check_id == "catalog_membership" and verdicts[0].status == "violated"


def test_catalog_membership_on_catalog_is_satisfied_and_checks_others():
    step = _step_with_intent(0, "get_account", "view_account", args={"account_id": "a1"}, side_effect="read")
    session = make_session(steps=[step], caller_role="Teller", channel="branch")
    verdicts = call.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    by_check = {v.check_id: v.status for v in verdicts}
    assert by_check["catalog_membership"] == "satisfied"
    assert by_check["entitlement"] == "satisfied"
    assert by_check["version_conformance"] == "satisfied"
    assert by_check["side_effect_class"] == "satisfied"


def test_entitlement_missing_caller_data_fails_open():
    step = _step_with_intent(0, "get_account", "view_account", args={"account_id": "a1"})
    session = make_session(steps=[step], caller_role="", channel="")
    verdicts = call.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["entitlement"] == "satisfied"


def test_entitlement_wrong_role_is_violated():
    step = _step_with_intent(0, "get_account", "view_account", args={"account_id": "a1"})
    session = make_session(steps=[step], caller_role="Applicant", channel="branch")
    verdicts = call.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["entitlement"] == "violated"


def test_entitlement_cites_catalog_policy_when_declared():
    entry = IntentEntry(intent="view_account", allowed_roles=("Teller",), policy=("4.1",))
    catalog = IntentCatalog(version="1", policy_document="bank_policy.md", intents={"view_account": entry})
    step = _step_with_intent(0, "get_account", "view_account", args={})
    session = make_session(steps=[step], caller_role="Applicant")
    verdicts = call.evaluate(session, catalog, make_ctx(session))
    v = {v.check_id: v for v in verdicts}["entitlement"]
    assert v.source == {"document": "bank_policy.md", "section": "4.1"}


def test_version_conformance_undeclared_param_is_violated():
    step = _step_with_intent(0, "get_account", "view_account", args={"account_id": "a1", "include_ssn": True})
    session = make_session(steps=[step], caller_role="Teller", channel="branch")
    verdicts = call.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    v = {v.check_id: v for v in verdicts}["version_conformance"]
    assert v.status == "violated" and "include_ssn" in v.detail


def test_side_effect_class_write_on_read_only_is_violated():
    step = _step_with_intent(0, "get_account", "view_account", args={"account_id": "a1"}, side_effect="write")
    session = make_session(steps=[step], caller_role="Teller", channel="branch")
    verdicts = call.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["side_effect_class"] == "violated"


# --- scope level -----------------------------------------------------------------

def test_field_scope_extra_field_is_violated():
    step = _step_with_intent(0, "get_account", "view_account", result={"account_id": "a1", "balance": 5, "ssn": "x"})
    session = make_session(steps=[step])
    verdicts = scope.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    v = {v.check_id: v for v in verdicts}["field_scope"]
    assert v.status == "violated" and "ssn" in v.detail


def test_field_scope_clean_is_satisfied():
    step = _step_with_intent(0, "get_account", "view_account", result={"account_id": "a1", "balance": 5})
    session = make_session(steps=[step])
    verdicts = scope.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["field_scope"] == "satisfied"


def test_filter_scope_wrong_owner_is_violated():
    step = _step_with_intent(0, "get_account", "view_account", result={"owner_id": "someone-else"})
    session = make_session(steps=[step], user_id="me")
    verdicts = scope.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["filter_scope"] == "violated"


def test_filter_scope_own_owner_is_satisfied():
    step = _step_with_intent(0, "get_account", "view_account", result={"owner_id": "me"})
    session = make_session(steps=[step], user_id="me")
    verdicts = scope.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["filter_scope"] == "satisfied"


def test_filter_scope_unwraps_columns_rows_result_shape():
    result = {"columns": ["owner_id"], "rows": [{"owner_id": "me"}, {"owner_id": "someone-else"}], "row_count": 2}
    step = _step_with_intent(0, "get_account", "view_account", result=result)
    session = make_session(steps=[step], user_id="me")
    verdicts = scope.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["filter_scope"] == "violated"


def test_volume_scope_bulk_is_violated():
    step = _step_with_intent(0, "get_account", "view_account", row_count=50)
    session = make_session(steps=[step])
    verdicts = scope.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["volume_scope"] == "violated"


def test_volume_scope_normal_is_satisfied():
    step = _step_with_intent(0, "get_account", "view_account", row_count=1)
    session = make_session(steps=[step])
    verdicts = scope.evaluate(session, _catalog(VIEW_ACCOUNT), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["volume_scope"] == "satisfied"


# --- session level -----------------------------------------------------------------

def test_toxic_combination_flags_declared_pair():
    a = IntentEntry(intent="view_applicant", allowed_roles=("Staff",), toxic_with=("export_directory",))
    b = IntentEntry(intent="export_directory", allowed_roles=("Manager",))
    steps = [_step_with_intent(0, "get_applicant", "view_applicant"), _step_with_intent(1, "export", "export_directory")]
    session = make_session(steps=steps)
    verdicts = session_checks.evaluate(session, _catalog(a, b), make_ctx(session))
    toxic = [v for v in verdicts if v.check_id == "toxic_combination"]
    assert any(v.status == "violated" for v in toxic)


def test_toxic_combination_clean_pair_is_satisfied():
    a = IntentEntry(intent="view_applicant", allowed_roles=("Staff",), toxic_with=("export_directory",))
    b = IntentEntry(intent="view_account", allowed_roles=("Staff",))
    steps = [_step_with_intent(0, "get_applicant", "view_applicant"), _step_with_intent(1, "get_account", "view_account")]
    session = make_session(steps=steps)
    verdicts = session_checks.evaluate(session, _catalog(a, b), make_ctx(session))
    toxic = [v for v in verdicts if v.check_id == "toxic_combination"]
    assert toxic and all(v.status == "satisfied" for v in toxic)


def test_goal_alignment_matched_descriptor_is_satisfied():
    entry = IntentEntry(intent="view_account", allowed_roles=("Teller",), trigger_descriptors=["check my balance"])
    step = _step_with_intent(0, "get_account", "view_account")
    turn = make_turn(0, user_message="can you check my balance please")
    session = make_session(steps=[step], turns=[turn])
    verdicts = session_checks.evaluate(session, _catalog(entry), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["goal_alignment"] == "satisfied"


def test_goal_alignment_unmatched_descriptor_is_flagged():
    entry = IntentEntry(intent="view_account", allowed_roles=("Teller",), trigger_descriptors=["check my balance"])
    step = _step_with_intent(0, "get_account", "view_account")
    turn = make_turn(0, user_message="what's the weather like today")
    session = make_session(steps=[step], turns=[turn])
    verdicts = session_checks.evaluate(session, _catalog(entry), make_ctx(session))
    v = {v.check_id: v for v in verdicts}["goal_alignment"]
    assert v.status == "violated" and v.effect == "flag"


def test_workflow_integrity_missing_obligation_is_violated():
    entry = IntentEntry(intent="decide_loan", allowed_roles=("Underwriter",), closing_obligation="send_notice")
    step = _step_with_intent(0, "decide", "decide_loan")
    session = make_session(steps=[step])
    verdicts = session_checks.evaluate(session, _catalog(entry), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["workflow_integrity"] == "violated"


def test_workflow_integrity_fulfilled_obligation_is_satisfied():
    entry = IntentEntry(intent="decide_loan", allowed_roles=("Underwriter",), closing_obligation="send_notice")
    notice = IntentEntry(intent="send_notice", allowed_roles=("Underwriter",))
    steps = [_step_with_intent(0, "decide", "decide_loan"), _step_with_intent(1, "notify", "send_notice")]
    session = make_session(steps=steps)
    verdicts = session_checks.evaluate(session, _catalog(entry, notice), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["workflow_integrity"] == "satisfied"


def test_redundancy_retry_storm_is_violated():
    steps = [_step_with_intent(i, "search", "search", args={"q": "x"}) for i in range(3)]
    session = make_session(steps=steps)
    verdicts = session_checks.evaluate(session, _catalog(), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["redundancy"] == "violated"


def test_redundancy_single_retry_is_satisfied():
    steps = [_step_with_intent(i, "search", "search", args={"q": "x"}) for i in range(2)]
    session = make_session(steps=steps)
    verdicts = session_checks.evaluate(session, _catalog(), make_ctx(session))
    assert {v.check_id: v.status for v in verdicts}["redundancy"] == "satisfied"


def test_redundancy_unique_call_not_applicable():
    steps = [_step_with_intent(0, "search", "search", args={"q": "x"})]
    session = make_session(steps=steps)
    verdicts = session_checks.evaluate(session, _catalog(), make_ctx(session))
    assert "redundancy" not in {v.check_id for v in verdicts}
