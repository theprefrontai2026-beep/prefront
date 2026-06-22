"""Domain-pack loader + resolver."""

from __future__ import annotations

from skillbuilder.domain_packs import list_pack_names, load_pack


def test_builtin_pack_is_listed_and_loads():
    assert "credit_collections" in list_pack_names()
    pack = load_pack("credit_collections")
    assert pack is not None
    assert pack.model.domain == "credit_collections"


def test_field_resolution_to_namespaces():
    pack = load_pack("credit_collections")
    assert pack.resolve_field("credit_status") == "column"
    assert pack.resolve_field("order_total") == "column"        # real datasource column
    assert pack.resolve_field("requested_fields") == "request_param"
    assert pack.resolve_field("available_credit") == "metric"
    assert pack.resolve_field("caller.role") == "caller"
    # aliases resolve to the same namespace as the canonical field
    assert pack.resolve_field("region") == "column"             # alias of region_id
    assert pack.resolve_field("order value") == "column"        # alias of order_total
    # genuinely unknown field
    assert pack.resolve_field("customer_tier") is None


def test_role_and_action_resolution():
    pack = load_pack("credit_collections")
    assert pack.resolve_role("RSM") == "regional_sales_manager"
    assert pack.resolve_role("Director, Credit & Collections") == "director_credit_collections"
    assert pack.resolve_role("nope") is None
    assert pack.intent_for_action("new order") == "create_order"
    assert "create_order" in pack.known_intents()


def test_missing_pack_returns_none():
    assert load_pack("does_not_exist") is None
