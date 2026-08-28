"""End-to-end wiring test for _call_governed's inline-checks integration
(autonomous_build.md step 18) - resolve_caller/govern/db are mocked (no real
Postgres), so this exercises the actual control flow in server.py, not just
governance/inline_checks.py in isolation (see test_inline_checks.py for that).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from semanticmcp import server
from semanticmcp.governance import inline_checks
from semanticmcp.governance.context import Caller, Decision, GovernanceContext

CATALOG_YAML = textwrap.dedent("""
    intent_catalog:
      version: 1
      intents:
        - intent: view_account
          side_effect: read
          params: [account_id]
          allowed_callers: {roles: [Teller], channels: []}
          fields: [account_id, balance]
""")


@pytest.fixture(autouse=True)
def _reset():
    inline_checks.reload()
    yield
    inline_checks.reload()


@pytest.fixture
def catalog_path(tmp_path: Path, monkeypatch):
    p = tmp_path / "intent_catalog.yaml"
    p.write_text(CATALOG_YAML)
    monkeypatch.setattr(inline_checks, "INTENT_CATALOG_PATH", str(p))
    return p


def _read_tool(**overrides) -> dict:
    base = {"name": "get_account", "intent": "view_account", "kind": "read",
            "sql": "SELECT account_id, balance FROM accounts WHERE account_id = :account_id",
            "template_id": "t1", "injected": []}
    base.update(overrides)
    return base


def _precheck_write_tool(**overrides) -> dict:
    base = {"name": "close_account", "intent": "view_account", "kind": "precheck",
            "sql": "SELECT account_id FROM accounts WHERE account_id = :account_id",
            "write_action": {"table": "accounts", "kind": "update", "params": ["account_id"],
                             "column_map": {}, "key_columns": ["account_id"]},
            "template_id": "t2", "injected": []}
    base.update(overrides)
    return base


async def _run(monkeypatch, tool, args, caller_role, rows=None):
    monkeypatch.setattr(server, "resolve_caller", lambda dsn: Caller(attrs={"role": caller_role}))
    monkeypatch.setattr(
        server, "govern",
        lambda **kw: GovernanceContext(intent=kw["intent"], kind=kw["kind"], args=kw["args"],
                                       caller=kw["caller"], decision=Decision(status="allowed")),
    )
    monkeypatch.setattr(server.db, "run_select", lambda dsn, sql, binds: rows or [])
    policy = SimpleNamespace(bundle=None)
    return await server._call_governed(tool, "dsn://fake", args, policy)


async def test_unentitled_caller_blocked_before_execution(monkeypatch, catalog_path):
    executed = {"called": False}

    def fake_run_select(dsn, sql, binds):
        executed["called"] = True
        return [{"account_id": 1, "balance": 500}]

    monkeypatch.setattr(server.db, "run_select", fake_run_select)
    result = await _run(monkeypatch, _read_tool(), {"account_id": 1}, "Applicant")
    assert result["status"] == "blocked"
    assert "inline_check_blocked" in " ".join(result["reasons"])
    assert executed["called"] is False, "an unentitled call must never reach the DB"


async def test_entitled_caller_executes(monkeypatch, catalog_path):
    result = await _run(monkeypatch, _read_tool(), {"account_id": 1}, "Teller",
                        rows=[{"account_id": 1, "balance": 500}])
    assert result["status"] == "allowed"
    assert result["rows"] == [{"account_id": 1, "balance": 500}]


async def test_side_effect_escalation_blocks_a_precheck_write(monkeypatch, catalog_path):
    # view_account is catalog-approved read-only; a precheck WITH a
    # write_action is a real write regardless of govern_kind's own "read vs
    # mcp_write" masking concept - side_effect_class must still catch it.
    result = await _run(monkeypatch, _precheck_write_tool(), {"account_id": 1}, "Teller",
                        rows=[{"account_id": 1}])
    assert result["status"] == "blocked"
    inline = result["governance"]["inline_checks"]
    hit = next(v for v in inline if v["check_id"] == "side_effect_class")
    assert hit["status"] == "violated"


async def test_annotate_decision_reads_inline_checks(monkeypatch, catalog_path):
    result = await _run(monkeypatch, _read_tool(), {"account_id": 1}, "Applicant")
    inline = result["governance"]["inline_checks"]
    assert any(v["check_id"] == "entitlement" and v["status"] == "violated" for v in inline)
