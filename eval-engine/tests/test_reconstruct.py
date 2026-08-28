import json

from evalengine.binding import load as load_binding
from evalengine.reconstruct import reconstruct

BINDING = load_binding()  # bundled default profile


def _span(span_id, parent_span_id, name, kind, start, **overrides):
    row = {
        "trace_id": "trace-1",
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": kind,
        "start_time": start,
        "end_time": start,
        "status": "OK",
        "attributes": {},
        "input_value": "",
        "output_value": "",
        "tool_name": "",
        "session_id": "sess-1",
        "user_id": "user-1",
        "user_role": "Applicant",
        "channel": "portal",
        "intent_name": "",
    }
    row.update(overrides)
    return row


def test_reconstruct_builds_turns_and_tool_steps_from_raw_spans():
    rows = [
        _span("root", "", "session sess-1", "", "2026-01-01T00:00:00"),
        _span("t0", "root", "turn 0", "", "2026-01-01T00:00:01",
             input_value="what is my balance?",
             output_value=json.dumps({"content": "Your balance is 500 dollars."})),
        _span("tool0", "t0", "tool get_balance", "TOOL", "2026-01-01T00:00:02",
             input_value=json.dumps({"account_id": "acct-1"}),
             output_value=json.dumps({"balance": 500}),
             tool_name="get_balance", intent_name="check_balance",
             attributes={"app.row_count": "1", "app.side_effect": "read"}),
    ]
    session = reconstruct("sess-1", rows, BINDING)
    assert session.session_id == "sess-1"
    assert session.user_id == "user-1"
    assert session.caller_role == "Applicant"
    assert len(session.turns) == 1
    assert session.turns[0].user_message == "what is my balance?"
    assert session.turns[0].assistant_message == "Your balance is 500 dollars."
    assert len(session.steps) == 1
    step = session.steps[0]
    assert step.tool_name == "get_balance"
    assert step.intent == "check_balance"
    assert step.args == {"account_id": "acct-1"}
    assert step.result == {"balance": 500}
    assert step.row_count == 1
    assert step.side_effect == "read"
    assert step.turn_seq == 0
    assert session.final_answer == "Your balance is 500 dollars."
