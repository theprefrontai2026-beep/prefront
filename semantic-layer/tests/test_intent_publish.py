"""Approving mined candidates into a real intent catalog.

This is where observation becomes permission, so these test the places where a
mistake would silently WIDEN a grant. Everything else on the mining path can be
wrong and produce a bad report; this one produces an artifact a runtime
enforces.
"""

from __future__ import annotations

import pytest

from semanticlayer.intent_publish import (
    ApprovedIntent,
    build_from_approved,
    render,
    to_entry,
)


def test_observed_callers_are_not_permitted_callers():
    """The safety property of the whole screen. Nothing about a candidate's
    observed roles reaches the artifact — a caller is permitted only because a
    human named it in `approved_roles`."""
    e = to_entry(ApprovedIntent(intent="x", steps=["a"]))
    assert e.allowed_callers.roles == []


def test_an_empty_role_list_is_flagged_loudly():
    """It is the widest possible grant wearing the narrowest look: Family 3
    treats an empty approved set as unrestricted on some paths, so publishing
    one by accident permits everybody while reading as permitting nobody."""
    _, problems = build_from_approved([ApprovedIntent(intent="x", steps=["a"])])
    assert any("NOT a deny" in p for p in problems)


def test_the_terminal_step_names_the_operation():
    """For a multi-call workflow the operation IS its terminal act; the earlier
    steps are the evidence gathered for it, not its identity."""
    e = to_entry(ApprovedIntent(intent="assess", steps=["find", "profile", "decide"]))
    assert e.tool_name == "decide"


def test_an_explicit_tool_name_wins_over_the_inference():
    e = to_entry(ApprovedIntent(intent="assess", steps=["a", "b"], tool_name="chosen"))
    assert e.tool_name == "chosen"


def test_a_duplicate_intent_is_dropped_not_silently_overwritten():
    """The catalog is keyed by intent, so a second entry would replace the
    first — discarding the approval of the one the reviewer actually saw."""
    cat, problems = build_from_approved([
        ApprovedIntent(intent="dup", steps=["first"], approved_roles=["A"]),
        ApprovedIntent(intent="dup", steps=["second"], approved_roles=["B"]),
    ])
    assert len(cat.intents) == 1
    assert cat.intents[0].tool_name == "first"          # the first survives
    assert any("duplicate intent" in p for p in problems)


def test_side_effect_is_normalised_to_the_two_the_schema_allows():
    assert to_entry(ApprovedIntent(intent="x", steps=["a"], side_effect="write")).side_effect == "write"
    assert to_entry(ApprovedIntent(intent="x", steps=["a"], side_effect="")).side_effect == "read"
    # Anything unrecognised is treated as a write: mislabelling a write as a
    # read removes a control, the reverse only adds one.
    assert to_entry(ApprovedIntent(intent="x", steps=["a"], side_effect="mutate")).side_effect == "write"


def test_the_model_s_reading_never_reaches_the_artifact():
    """An inferred policy sentence is a reading for a human. It is not a fact
    and has no business in a file the runtime enforces."""
    e = to_entry(ApprovedIntent(intent="x", steps=["a"], note="the model thinks this requires X"))
    assert "the model thinks" not in e.model_dump_json()
    assert e.policy == []


def test_the_header_says_the_catalog_was_mined():
    """The same file to the runtime; a very different thing to a person reading
    it a year later. The header is the only place that survives."""
    cat, _ = build_from_approved([ApprovedIntent(intent="x", steps=["a"], approved_roles=["A"])])
    text = render(cat)
    assert "MINED FROM OBSERVED BEHAVIOUR" in text
    assert "NOT the set of callers observed" in text
    assert "intent_catalog:" in text          # ...and it is still the real schema


def test_an_unnamed_candidate_is_skipped_with_a_reason():
    cat, problems = build_from_approved([ApprovedIntent(intent="  ", steps=["a"])])
    assert cat.intents == [] and any("no intent name" in p for p in problems)
