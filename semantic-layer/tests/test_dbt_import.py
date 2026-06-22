"""End-to-end test for the dbt semantic-model importer.

Drives a real customer dbt model + Prefront overlay through the full deterministic
pipeline against the CommerceRisk schema, asserting the governance invariants hold:

  * the imported model passes the SAME publish-time validate() gate as the LLM path;
  * a dbt join NOT backed by a real foreign key is dropped + reported (never shipped);
  * overlay sensitivity (+ schema [SENSITIVE] markers) restrict attributes so they
    never appear in a generated template's SELECT / result columns;
  * an aggregate measure with no stored column is reported, not invented.

Runnable under pytest or directly: ``python tests/test_dbt_import.py``.
"""

from __future__ import annotations

from pathlib import Path

from semanticlayer.catalog import build_catalog_from_file
from semanticlayer.pipeline import run_import_pipeline

_FIX = Path(__file__).parent / "fixtures"
_SCHEMA = Path("/home/sachi/prefront/commercerisk-demo/db/schema.sql")


def _run():
    catalog = build_catalog_from_file(_SCHEMA, datasource_id="commercerisk")
    dbt = (_FIX / "commercerisk_dbt_semantic_models.yaml").read_text(encoding="utf-8")
    overlay = (_FIX / "commercerisk_overlay.yaml").read_text(encoding="utf-8")
    return run_import_pipeline(
        dbt, overlay, catalog, model_id="commercerisk_semantic_model", domain="commercerisk",
    ), catalog


def test_import_passes_validation():
    result, _ = _run()
    assert result.validation.ok, f"validation failed: {result.validation.errors}"
    assert result.model.status == "published"
    # Every overlay intent produced a governed tool.
    tool_intents = {t.source_intent for t in result.tools}
    assert {"find_customers", "get_customer_credit", "create_order"} <= tool_intents


def test_non_fk_join_dropped_and_reported():
    result, _ = _run()
    dropped = result.report["relationships_dropped"]
    # The bogus customers.industry -> products.product_id join has no FK backing.
    assert any("industry" in d["from"] and "no real foreign key" in d["reason"] for d in dropped), \
        f"expected the non-FK join to be dropped, got {dropped}"
    # The real FK-backed joins survived and are approved.
    approved_pairs = {(r.from_entity, r.to_entity) for r in result.relationships}
    assert ("customers", "regions") in approved_pairs
    assert ("orders", "customers") in approved_pairs
    assert all(r.approved for r in result.relationships)


def test_sensitive_attributes_never_in_templates():
    result, _ = _run()
    restricted_cols = {s.physical_column.lower() for s in result.sensitivity}
    # tax_id ([SENSITIVE] marker + rule) and credit_limit/current_balance (overlay).
    assert "customers.tax_id" in restricted_cols
    assert "customers.credit_limit" in restricted_cols
    assert "customers.current_balance" in restricted_cols
    # No read template's SELECT may expose a restricted column.
    for t in result.templates:
        if t.kind != "read":
            continue
        names = {rc.name.lower() for rc in t.result_columns}
        assert "tax_id" not in names, f"{t.template_id} leaks tax_id"
        assert "credit_limit" not in names, f"{t.template_id} leaks credit_limit"


def test_aggregate_measure_reported_not_invented():
    result, _ = _run()
    warnings = " ".join(result.report["warnings"])
    assert "total_exposure" in warnings and "metrics" in warnings


if __name__ == "__main__":
    result, catalog = _run()
    rep = result.report
    print(f"validation_ok = {result.validation.ok}")
    print(f"errors        = {result.validation.errors}")
    print(f"entities      = {[e.entity_key for e in result.model.entities]}")
    print(f"relationships = {[(r.from_entity, r.to_entity) for r in result.relationships]}")
    print(f"dropped joins = {[(d['from'], d['reason']) for d in rep['relationships_dropped']]}")
    print(f"sensitivity   = {rep['sensitivity']}")
    print(f"intents       = {rep['intents']}")
    print(f"templates     = {[(t.template_id, t.kind) for t in result.templates]}")
    print(f"tools         = {[t.tool_name for t in result.tools]}")
    print("warnings:")
    for w in rep["warnings"]:
        print(f"  - {w}")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
