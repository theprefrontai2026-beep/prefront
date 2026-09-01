"""Domain-level persistence: Finding/ConformanceTag objects in, ClickHouse
rows out. api.py and worker.py talk to this module, never to ch.py directly,
so the on-disk shape stays swappable without touching either caller.
"""

from __future__ import annotations

from typing import Any

from . import ch
from .checks import CheckSettings
from .combinator import conformance_tags as _derive_tags
from .combinator import violations as _derive_violations
from .contract import Finding


def ensure_schema() -> None:
    ch.ensure_schema()


def persist(findings: list[Finding]) -> dict[str, int]:
    n_verdicts = ch.insert_verdicts(findings)
    n_tags = ch.insert_conformance_tags(_derive_tags(findings))
    return {"verdicts": n_verdicts, "violations": len(_derive_violations(findings)), "conformance_tags": n_tags}


def is_evaluated(session_id: str, version_key: str) -> bool:
    return ch.is_evaluated(session_id, version_key)


def mark_evaluated(session_id: str, version_key: str) -> None:
    ch.mark_evaluated(session_id, version_key)


def session_spans(session_id: str) -> list[dict[str, Any]]:
    return ch.session_spans(session_id)


def candidate_sessions(quiet_seconds: float, limit: int = 200) -> list[dict[str, Any]]:
    return ch.candidate_sessions(quiet_seconds, limit)


def session_shapes(scenario_id: str) -> list[dict[str, Any]]:
    return ch.session_shapes(scenario_id)


def verdict_history(rule_id: str = "", check_id: str = "", limit: int = 500) -> list[dict[str, Any]]:
    return ch.verdict_history(rule_id=rule_id, check_id=check_id, limit=limit)


def rule_fire_counts(family: str = "family1", since: int = 0) -> dict[str, int]:
    return ch.rule_fire_counts(family, since)


def list_verdicts(**kwargs) -> dict[str, Any]:
    return ch.list_verdicts(**kwargs)


def list_findings(**kwargs) -> dict[str, Any]:
    return ch.list_findings(**kwargs)


def list_feed(**kwargs) -> dict[str, Any]:
    return ch.list_feed(**kwargs)


def session_conformance(session_id: str) -> list[dict[str, Any]]:
    return ch.session_conformance(session_id)


def list_conformance(**kwargs) -> dict[str, Any]:
    return ch.list_conformance(**kwargs)


def totals(since: int = 0) -> dict[str, Any]:
    return ch.totals(since)


def verdict_rows_for_report(since: int = 0, cap: int = 20000) -> tuple[list[dict[str, Any]], bool]:
    return ch.verdict_rows_for_report(since, cap)


def retention_facts() -> dict[str, str]:
    """table -> TTL expression for every store the report speaks for: this
    service's three tables plus the shared `spans` table oob-ingest owns."""
    out = ch.table_ttls(ch._RETENTION_TABLES)
    out["spans"] = ch.spans_ttl()
    return out


def truncate() -> None:
    ch.truncate()


# --- check enablement (evalengine.checks) -----------------------------------

_CHECKS_KEY = "checks"


def load_check_settings() -> CheckSettings:
    """The stored disabled-check set, or "everything on" when nothing has
    been saved. A missing row, unreadable JSON or an id from a build that no
    longer has that check all degrade to a valid settings object rather than
    an error - configuration gaps never fail the engine (Hard Rule 9)."""
    return CheckSettings.from_json(ch.read_setting(_CHECKS_KEY))


def save_check_settings(settings: CheckSettings) -> CheckSettings:
    ch.write_setting(_CHECKS_KEY, settings.to_json())
    return settings


def clear_check_settings() -> None:
    ch.delete_setting(_CHECKS_KEY)
