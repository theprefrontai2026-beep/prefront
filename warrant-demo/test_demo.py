"""The demo is a regression suite, not just a presentation.

Two things are being protected. The obvious one: every situation still
produces the decisions the copy on screen claims it produces, so a change to
the engine cannot quietly turn a live demo into a lie. The subtler one: the
demo reaches the engine only through its public surface, which is what makes
"the same engine governs a different application unchanged" a testable claim
rather than a slogan.

Offline, no network, no container. Runs in `make test`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "warrant"))
# `remote.py` (the HTTP mode) imports the service's client. Only the drift
# guard below reaches it, and only when pyyaml is present, so the demo's own
# minimal-dependency property is untouched.
sys.path.insert(0, str(HERE.parent / "warrant-service"))

import runner  # noqa: E402
import scenarios  # noqa: E402
import world  # noqa: E402


@pytest.fixture(scope="module")
def results():
    return runner.run_all()


# --- the demo says what it does --------------------------------------------


@pytest.mark.parametrize("scenario", scenarios.CATALOGUE, ids=lambda s: s.key)
def test_each_situation_decides_exactly_as_documented(scenario):
    """The copy beside each run names an outcome. This is that copy, asserted."""
    result = runner.run(scenario)
    got = tuple(s.effect for s in result.governed.steps)
    assert got == scenario.expect, (
        f"{scenario.key} now decides {got}, but the demo tells the audience "
        f"{scenario.expect}"
    )


def test_the_governed_lane_loses_no_money_anywhere(results):
    """The headline claim on the page: four incidents become zero."""
    assert all(r.governed.unauthorized_cents == 0 for r in results)
    assert sum(r.ungoverned.unauthorized_cents for r in results) > 0


def test_the_baseline_is_genuinely_unimpeded(results):
    """A control that blocks everything proves nothing, and an audience that
    has not seen the allow path does not believe the deny path."""
    base = next(r for r in results if r.scenario.key == "BASE-01")
    assert base.governed.blocked == 0
    assert base.governed.held == 0
    assert base.governed.ledger.cash_moved_cents == base.ungoverned.ledger.cash_moved_cents


def test_the_marquee_case_fires_only_the_provenance_control():
    """INJ-02's whole point, and the page says so in as many words: identity,
    scope, counterparty and budget all pass. If another control starts firing,
    the claim is no longer true even though the outcome looks the same."""
    result = runner.run(scenarios.BY_KEY["INJ-02"])
    denial = result.governed.steps[-1]
    assert denial.effect == "deny"
    assert denial.reasons == ("pds.injection_tripwire",)


def test_the_redirect_case_is_caught_twice_over(results):
    """INJ-01 claims defence in depth: two independent controls, either of
    which would have been enough."""
    result = runner.run(scenarios.BY_KEY["INJ-01"])
    reasons = set(result.governed.steps[-1].reasons)
    assert {"pds.counterparty_scope", "pds.injection_tripwire"} <= reasons


def test_a_boundary_case_asks_a_human_rather_than_refusing(results):
    """Step-up has to survive as a distinct outcome, or the demo collapses
    into "it blocks things"."""
    held = [r for r in results if r.governed.held]
    assert {r.scenario.key for r in held} == {"BND-01", "BND-02"}
    for r in held:
        step = next(s for s in r.governed.steps if s.outcome == "held_for_approval")
        assert step.detail, "a step-up must be able to name what changed"


# --- the two lanes are genuinely comparable ---------------------------------


@pytest.mark.parametrize("scenario", scenarios.CATALOGUE, ids=lambda s: s.key)
def test_both_lanes_run_the_identical_call_sequence(scenario):
    """The demo's central claim is that the enforcement plane is the ONLY
    difference between the columns. If the lanes ever ran different calls, the
    contrast would be an artefact of the harness."""
    result = runner.run(scenario)
    assert [s.call.action for s in result.ungoverned.steps] == [
        s.call.action for s in result.governed.steps
    ]
    assert [s.call.amount_cents for s in result.ungoverned.steps] == [
        s.call.amount_cents for s in result.governed.steps
    ]


def test_running_twice_gives_the_same_answer():
    """A demo that drifts between rehearsal and stage is worse than no demo."""
    first = runner.summary(runner.run_all())
    second = runner.summary(runner.run_all())
    assert first == second


# --- the engine stays reusable ---------------------------------------------


def test_the_demo_touches_only_the_engines_public_surface():
    """No private imports, no monkeypatching, no subclassing.

    This is the test that makes "drop the same engine into another
    application" a claim rather than a hope: if the demo needed a private hook,
    the next customer would need one too.
    """
    offenders = []
    for path in sorted(HERE.glob("*.py")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"from warrant[.\w]* import .*\b_\w", line):
                offenders.append(f"{path.name}:{i} private import")
            if re.search(r"\bwarrant\.\w*\._\w", line):
                offenders.append(f"{path.name}:{i} private attribute")
            if re.search(r"class \w+\((?:warrant\.)?(?:PolicyDecisionService|TaskTree|MissionAuthority)", line):
                offenders.append(f"{path.name}:{i} subclasses an engine type")
    assert not offenders, offenders


def test_the_engine_is_not_modified_by_running_the_demo():
    """The registry and the catalogue are inputs; running must not mutate them."""
    before = world.ACTIONS.names()
    runner.run(scenarios.BY_KEY["INJ-01"])
    assert world.ACTIONS.names() == before


# --- money is handled the way the engine demands ----------------------------


def test_every_amount_in_the_world_is_integer_cents():
    """The engine refuses float budgets. A demo that carried dollars as floats
    and rounded at the boundary would be demonstrating a bug."""
    for invoice in world.INVOICES.values():
        assert isinstance(invoice.amount_cents, int)
    for scenario in scenarios.CATALOGUE:
        for call in scenario.build(__import__("deployment").build(tree_id=f"probe-{scenario.key}")):
            assert isinstance(call.amount_cents, int)


def test_the_console_and_the_server_agree_on_the_payload_shape():
    """The page reads specific keys; a rename here is invisible until a demo."""
    import server

    payload = server.build_payload()
    assert payload["open_on"] in scenarios.BY_KEY
    assert set(payload["mission"]) >= {
        "operator", "instruction", "actions", "counterparties", "budget", "window", "max_depth"
    }
    first = payload["scenarios"][0]
    assert set(first) >= {"key", "group", "title", "question", "lanes", "matched"}
    assert set(first["lanes"]["governed"]) >= {"steps", "executed", "blocked", "held", "cash_moved"}
    step = first["lanes"]["governed"]["steps"][0]
    assert set(step) >= {"narrative", "outcome", "effect", "reasons", "checks", "sources"}


def test_the_console_reads_only_keys_the_server_sends():
    """Guards the pair from the other side: every `RUN.x` / `.lanes.x` the page
    dereferences must exist in the payload."""
    import server

    payload = server.build_payload()
    html = (HERE / "console.html").read_text()
    for key in re.findall(r"\bRUN\.(\w+)", html):
        assert key in payload, f"console.html reads RUN.{key}, which the server does not send"


def test_the_generated_action_registry_matches_world_actions():
    """The file warrant-service reads and the registry the embedded demo uses
    must not drift, or the two modes would decide differently for a reason
    that has nothing to do with the service.

    Skipped rather than failed without PyYAML: the demo deliberately runs from
    the standard library plus `cryptography` so it starts on any machine, and
    this check is a maintenance guard rather than part of what the demo does.
    """
    yaml = pytest.importorskip("yaml", reason="pyyaml is not a demo dependency")
    import remote

    on_disk = yaml.safe_load((HERE / "policy" / "action_registry.yaml").read_text())
    assert on_disk == remote.registry_document(), (
        "policy/action_registry.yaml is stale — run `python3 gen_registry.py`"
    )
