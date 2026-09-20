"""Task-tree invariants: narrowing, depth, the shared ledger, revocation.

Each section corresponds to a buyer question the spec poses, and the tests are
written so a failure reads as the answer changing rather than as an assertion
breaking.
"""

from __future__ import annotations

import threading

import pytest

from conftest import NOW
from warrant import Budget, Mission, TreeStore
from warrant.tree import BudgetExceeded, Grant, TaskTree, TreeError


def mission(**kw) -> Mission:
    base = dict(
        mission_id="m", subject="u", issuer="i", instruction_hash="h",
        budget=Budget(1_000, "USD", 10), max_depth=2,
    )
    base.update(kw)
    return Mission(**base)


# --- narrowing: "can a sub-agent escalate?" ---------------------------------


def test_a_child_grant_cannot_contain_what_the_parent_lacked():
    parent = Grant(action_classes=("a", "b"))
    child = parent.narrow(Grant(action_classes=("b", "c")))
    assert child.action_classes == ("b",)


def test_requesting_everything_inherits_the_parent_rather_than_the_world():
    """The line that carries the invariant. A sub-agent asking for an
    unconstrained grant is asking to inherit, and inheriting is the only
    reading of that request which cannot widen."""
    parent = Grant(action_classes=("a",), counterparties=("x",))
    child = parent.narrow(Grant())
    assert child.action_classes == ("a",)
    assert child.counterparties == ("x",)


def test_an_unconstrained_parent_can_be_narrowed_by_the_child():
    assert Grant().narrow(Grant(action_classes=("a",))).action_classes == ("a",)


def test_narrowing_is_transitive_down_a_chain():
    g = Grant(action_classes=("a", "b", "c"))
    assert g.narrow(Grant(action_classes=("a", "b"))).narrow(
        Grant(action_classes=("b", "c"))
    ).action_classes == ("b",)


def test_narrowing_preserves_the_requested_order():
    """Sorting would make two equivalent grants look different in a diff."""
    parent = Grant(action_classes=("a", "b", "c"))
    assert parent.narrow(Grant(action_classes=("c", "a"))).action_classes == ("c", "a")


# --- depth: "how far can this go?" ------------------------------------------


def test_spawning_past_the_missions_depth_cap_is_refused():
    tree = TaskTree("t", mission(max_depth=1), "root")
    child = tree.spawn(tree.root_id, "sub")
    with pytest.raises(TreeError, match="max_depth"):
        tree.spawn(child.node_id, "sub-sub")


def test_a_mission_that_does_not_mention_delegation_does_not_authorize_it():
    tree = TaskTree("t", mission(max_depth=0), "root")
    with pytest.raises(TreeError, match="max_depth"):
        tree.spawn(tree.root_id, "sub")


def test_the_actor_chain_is_carried_in_full():
    """Stored rather than walked, because retention may have pruned the
    intermediate nodes by the time an incident review reads the record."""
    tree = TaskTree("t", mission(), "root")
    child = tree.spawn(tree.root_id, "sub")
    assert tree.spawn(child.node_id, "sub-sub").actor_chain == ("root", "sub", "sub-sub")


def test_a_revoked_tree_cannot_grow():
    tree = TaskTree("t", mission(), "root")
    tree.revoke(NOW)
    with pytest.raises(TreeError, match="revoked"):
        tree.spawn(tree.root_id, "sub")


# --- the ledger: "can a leaked token drain me?" -----------------------------


def test_the_budget_is_the_trees_not_the_nodes():
    """Fanning out must not multiply the ceiling."""
    tree = TaskTree("t", mission(budget=Budget(100, "USD")), "root")
    a = tree.spawn(tree.root_id, "a")
    tree.reserve(60)
    with pytest.raises(BudgetExceeded):
        tree.reserve(60)  # a different branch, the same ledger


def test_an_in_flight_reservation_counts_against_a_concurrent_call():
    """A ceiling checked only against settled spend is not a ceiling: two
    branches deciding at the same instant would each see the full budget."""
    tree = TaskTree("t", mission(budget=Budget(100, "USD")), "root")
    tree.reserve(80)
    assert tree.remaining_minor() == 20
    assert tree.would_exceed(30)


def test_releasing_a_reservation_returns_the_money_but_not_the_call():
    """`max_calls` bounds ATTEMPTS — otherwise a failing agent retries forever
    for free, which is the retry storm the judgement plane has a check for."""
    tree = TaskTree("t", mission(budget=Budget(100, "USD", 5)), "root")
    handle = tree.reserve(80)
    tree.release(handle)
    assert tree.remaining_minor() == 100
    assert tree.call_count == 1


def test_settling_less_than_reserved_returns_the_difference():
    tree = TaskTree("t", mission(budget=Budget(100, "USD")), "root")
    tree.settle(tree.reserve(80), 50)
    assert tree.spend_committed == 50
    assert tree.spend_outstanding == 0
    assert tree.remaining_minor() == 50


def test_settling_more_than_reserved_is_refused():
    """Exactly the shape of an overspend sneaking past a ceiling that was
    checked against a smaller number."""
    tree = TaskTree("t", mission(budget=Budget(1_000, "USD")), "root")
    handle = tree.reserve(10)
    with pytest.raises(TreeError, match="cannot spend more than it reserved"):
        tree.settle(handle, 999)
    # ...and the hold survives the rejection, so the next call is still bounded.
    assert tree.spend_outstanding == 10


def test_a_reservation_cannot_be_settled_twice():
    tree = TaskTree("t", mission(), "root")
    handle = tree.reserve(10)
    tree.settle(handle)
    with pytest.raises(TreeError, match="already-settled"):
        tree.settle(handle)


def test_a_zero_budget_means_uncapped_not_forbidden():
    """A Mission authorizing only reads has no meaningful amount; reading the
    default as a hard zero would deny every such call."""
    tree = TaskTree("t", mission(budget=Budget()), "root")
    assert tree.remaining_minor() is None
    assert not tree.would_exceed(10**9)


def test_concurrent_reservations_cannot_oversubscribe_the_budget():
    """`reserve` is a check-then-act, and the whole point of reserving is that
    two branches reach it at the same instant."""
    tree = TaskTree("t", mission(budget=Budget(1_000, "USD", 0)), "root")
    granted, refused = [], []

    def take():
        try:
            granted.append(tree.reserve(100))
        except BudgetExceeded:
            refused.append(1)

    threads = [threading.Thread(target=take) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(granted) == 10          # 1000 / 100, exactly
    assert len(refused) == 40
    assert tree.spend_total == 1_000


# --- revocation: "who presses stop?" ----------------------------------------


def test_revocation_is_idempotent_and_the_first_reason_wins():
    """A human hitting stop and an anomaly signal will fire at nearly the same
    moment; the second must not fail, and must not overwrite the first cause."""
    tree = TaskTree("t", mission(), "root")
    tree.revoke(10, "spend velocity anomaly")
    tree.revoke(20, "cleanup")
    assert tree.revoked_reason == "spend velocity anomaly"


def test_liveness_covers_both_revocation_and_the_mission_window():
    """An expired Mission is exactly as dead as a revoked tree, and checking
    one without the other is the mistake this single method prevents."""
    tree = TaskTree("t", mission(not_before=NOW, not_after=NOW + 10), "root")
    assert tree.is_live_at(NOW + 5)
    assert not tree.is_live_at(NOW + 50)
    tree.revoke(NOW)
    assert not tree.is_live_at(NOW + 5)


def test_a_store_can_revoke_a_tree_it_has_never_seen():
    """Revocation has to work "even when an issuer does not cooperate"."""
    store = TreeStore()
    store.revoke("tree-we-never-created", NOW)
    assert store.is_denied("tree-we-never-created")


def test_a_store_can_be_enumerated():
    """A store that cannot be listed forces every consumer to keep a parallel
    index, which drifts the first time something is created by another path."""
    store = TreeStore()
    store.create("a", mission(), "root")
    store.create("b", mission(), "root")
    assert store.tree_ids() == ("a", "b")


def test_enumeration_excludes_trees_this_replica_never_saw():
    """A denylisted id is a revocation, not a task. Listing it would show an
    operator a task that never ran here."""
    store = TreeStore()
    store.create("a", mission(), "root")
    store.revoke("never-seen-here", NOW)
    assert store.tree_ids() == ("a",)
    assert "never-seen-here" in store.denylist()


def test_a_revoked_tree_id_is_never_reused():
    """Otherwise revocation could be undone by starting the task again."""
    store = TreeStore()
    store.revoke("t", NOW)
    with pytest.raises(TreeError, match="denylist"):
        store.create("t", mission(), "root")


def test_merging_a_denylist_keeps_the_earliest_revocation_time():
    """The earliest is what bounds the exposure window in an incident report."""
    store = TreeStore()
    store.merge_denylist({"t": 500})
    store.merge_denylist({"t": 100})
    assert store.denylist()["t"] == 100


def test_merging_reports_how_many_entries_were_new():
    store = TreeStore()
    assert store.merge_denylist({"a": 1, "b": 2}) == 2
    assert store.merge_denylist({"a": 1, "c": 3}) == 1


def test_a_merged_denylist_revokes_a_tree_this_replica_does_hold():
    store = TreeStore()
    tree = store.create("t", mission(), "root")
    store.merge_denylist({"t": NOW})
    assert tree.revoked
