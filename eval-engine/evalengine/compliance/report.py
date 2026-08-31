"""Fold verdict rows into per-control compliance states.

Pure: (packs, overlay, verdict rows, facts) -> report dict. No I/O, no clock
(Hard Rule 3 applies here as much as to a check). The three honest outcomes:

    evidenced       satisfied verdicts, none violated
    violated        at least one violated verdict
    indeterminate   only indeterminate verdicts (or a store fact half-present)
    no_evidence     the checks could run but produced nothing in the window
    unbound         the control keys on a data class the overlay binds no
                    columns to - nothing can be said about it
    not_configured  every check behind the control belongs to a family with
                    no artifact loaded (Hard Rule 9: zero verdicts, not an error)

"no_evidence" is never rendered as "clean" - absence of a verdict is "not
applicable" (Hard Rule 16), and a report that turned it into a pass would
be the false claim compliance_design.md §2.3 warns against.

Evidence on a row is span-id references + the verdict's own excerpt/detail,
never a payload (Hard Rule 8) - the report points into the store.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable

from .classes import CONTROL_CLASS_CHECKS, FAMILY_OF_CHECK, FIELD_AWARE_CHECKS, STORE_BASED_CLASSES
from .overlay import Overlay
from .packs import Control, FrameworkPack

STATES = ("violated", "evidenced", "indeterminate", "no_evidence", "unbound", "not_configured")
_SAMPLE_LIMIT = 5
_DETAIL_CHARS = 240


def _configured_families(facts: dict[str, Any]) -> set[str]:
    fams = {"family2"}
    if facts.get("rule_pack_configured"):
        fams.add("family1")
    if facts.get("catalog_configured"):
        fams.add("family3")
    return fams


def _field_match(detail: str, names: tuple[str, ...]) -> bool:
    d = (detail or "").lower()
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(n)}(?![a-z0-9_])", d) for n in names)


def _section_re(section: str) -> re.Pattern[str] | None:
    s = section.strip().lstrip("§").strip()
    if not s:
        return None
    return re.compile(rf"(?<![\d.]){re.escape(s)}(?![\d])")


def _source_section(row: dict[str, Any]) -> str:
    raw = row.get("source")
    if not raw:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("section") or "")
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    return str(d.get("section") or "") if isinstance(d, dict) else ""


def _sample(row: dict[str, Any]) -> dict[str, Any]:
    detail = str(row.get("detail") or "")
    return {
        "session_id": row.get("session_id", ""),
        "check_id": row.get("check_id", ""),
        "rule_id": row.get("rule_id", ""),
        "status": row.get("status", ""),
        "effect": row.get("effect", ""),
        "event_id": row.get("event_id", ""),
        "evidence_span_ids": list(row.get("evidence_span_ids") or ()),
        "evidence_excerpt": row.get("evidence_excerpt", ""),
        "detail": detail[:_DETAIL_CHARS] + ("…" if len(detail) > _DETAIL_CHARS else ""),
        "evaluated_at": row.get("evaluated_at", ""),
    }


def _samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"violated": 0, "indeterminate": 1, "satisfied": 2}
    # newest first, then a stable sort so violated rows lead
    newest = sorted(rows, key=lambda r: str(r.get("evaluated_at") or ""), reverse=True)
    ranked = sorted(newest, key=lambda r: order.get(str(r.get("status")), 3))
    return [_sample(r) for r in ranked[:_SAMPLE_LIMIT]]


def _counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter()
    sessions: set[str] = set()
    for r in rows:
        c[str(r.get("status") or "")] += 1
        sessions.add(str(r.get("session_id") or ""))
    return {
        "satisfied": c.get("satisfied", 0),
        "violated": c.get("violated", 0),
        "indeterminate": c.get("indeterminate", 0),
        "sessions": len(sessions - {""}),
    }


def _state_from_counts(counts: dict[str, int]) -> str:
    if counts["violated"]:
        return "violated"
    if counts["satisfied"]:
        return "evidenced"
    if counts["indeterminate"]:
        return "indeterminate"
    return "no_evidence"


def _check_control(control_class: str, data_class: str, overlay: Overlay,
                   by_check: dict[str, list[dict[str, Any]]], facts: dict[str, Any]) -> dict[str, Any]:
    check_ids = CONTROL_CLASS_CHECKS.get(control_class, ())
    families = _configured_families(facts)
    runnable = [c for c in check_ids if FAMILY_OF_CHECK.get(c) in families]
    row: dict[str, Any] = {
        "control_class": control_class, "data_class": data_class, "basis": "checks",
        "check_ids": list(check_ids), "runnable_check_ids": runnable,
        "scoping": "field" if data_class and any(c in FIELD_AWARE_CHECKS for c in check_ids) else "check",
        "counts": {"satisfied": 0, "violated": 0, "indeterminate": 0, "sessions": 0},
        "samples": [], "note": "",
    }
    if not runnable:
        row["state"] = "not_configured"
        missing = sorted({FAMILY_OF_CHECK.get(c, "?") for c in check_ids} - families)
        row["note"] = "no artifact loaded for " + ", ".join(missing) if missing else "no check evidences this control class"
        return row
    names: tuple[str, ...] = ()
    if data_class:
        names = overlay.field_names_for(data_class)
        if not names:
            row["state"] = "unbound"
            row["note"] = f"data class {data_class!r} binds no columns in the overlay"
            return row
    rows: list[dict[str, Any]] = []
    for c in runnable:
        for r in by_check.get(c, ()):
            if names and c in FIELD_AWARE_CHECKS and not _field_match(str(r.get("detail") or ""), names):
                continue
            rows.append(r)
    row["counts"] = _counts(rows)
    row["state"] = _state_from_counts(row["counts"])
    row["samples"] = _samples(rows)
    if row["state"] == "no_evidence":
        row["note"] = "checks configured, nothing exercised them in the window"
    return row


def _store_control(control_class: str, data_class: str, all_rows: list[dict[str, Any]],
                   facts: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "control_class": control_class, "data_class": data_class, "basis": "store",
        "check_ids": [], "runnable_check_ids": [], "scoping": "store",
        "counts": {"satisfied": 0, "violated": 0, "indeterminate": 0, "sessions": 0},
        "samples": [], "note": "",
    }
    if control_class == "audit_logging":
        n = len(all_rows)
        sessions = len({str(r.get("session_id") or "") for r in all_rows} - {""})
        row["counts"] = {"satisfied": n, "violated": 0, "indeterminate": 0, "sessions": sessions}
        row["state"] = "evidenced" if n else "no_evidence"
        row["note"] = (f"{n} version-stamped verdict(s) across {sessions} session(s) in the window; "
                       "the verdict store is the record. Tamper-evidence of this store is not asserted.")
        return row
    if control_class == "retention":
        ttls: dict[str, str] = facts.get("retention") or {}
        with_ttl = sorted(t for t, e in ttls.items() if e)
        without = sorted(t for t, e in ttls.items() if not e)
        if ttls and not without:
            row["state"] = "evidenced"
        elif with_ttl:
            row["state"] = "indeterminate"
        else:
            row["state"] = "no_evidence"
        row["note"] = ("TTL on: " + ", ".join(f"{t} ({ttls[t]})" for t in with_ttl) if with_ttl else "no table carries a TTL") \
            + (("; none on: " + ", ".join(without)) if without and with_ttl else "")
        return row
    if control_class == "change_management":
        if not (facts.get("rule_pack_configured") or facts.get("catalog_configured")):
            row["state"] = "not_configured"
            row["note"] = "no versioned artifact (rule pack / intent catalog) loaded"
            return row
        versions = sorted({str(r.get("rule_pack_version") or "") for r in all_rows} - {""}) \
            + sorted({str(r.get("catalog_version") or "") for r in all_rows} - {""})
        row["counts"]["satisfied"] = len(all_rows) if versions else 0
        row["state"] = "evidenced" if versions else "no_evidence"
        row["note"] = ("verdicts stamped with artifact versions: " + ", ".join(versions)) if versions \
            else "no verdict in the window carries an artifact version"
        return row
    row["state"] = "no_evidence"
    row["note"] = "no store fact evidences this class"
    return row


def _control_row(control_class: str, data_class: str, overlay: Overlay,
                 by_check: dict[str, list[dict[str, Any]]], all_rows: list[dict[str, Any]],
                 facts: dict[str, Any]) -> dict[str, Any]:
    if control_class in STORE_BASED_CLASSES:
        return _store_control(control_class, data_class, all_rows, facts)
    return _check_control(control_class, data_class, overlay, by_check, facts)


def _summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter(str(r.get("state")) for r in rows)
    out = {s: c.get(s, 0) for s in STATES}
    out["total"] = sum(out.values())
    return out


def _framework_report(pack: FrameworkPack, overlay: Overlay, by_check, all_rows, facts) -> dict[str, Any]:
    controls = []
    for c in pack.controls:
        row = _control_row(c.control_class, c.data_class, overlay, by_check, all_rows, facts)
        row.update({"id": c.id, "title": c.title})
        if c.note:
            row["pack_note"] = c.note
        controls.append(row)
    return {
        "framework": pack.framework, "title": pack.title, "version": pack.version,
        "out_of_scope": list(pack.out_of_scope),
        "summary": _summary(controls), "controls": controls,
    }


def _regime_report(overlay: Overlay, by_check, all_rows, facts) -> list[dict[str, Any]]:
    out = []
    for regime in overlay.domain_regime:
        bindings = []
        for b in regime.bindings:
            row = _control_row(b.control_class, b.data_class, overlay, by_check, all_rows, facts)
            row["policy_section"] = b.policy_section
            if b.note:
                row["binding_note"] = b.note
            pat = _section_re(b.policy_section)
            cited = [r for r in all_rows if pat and pat.search(_source_section(r))] if pat else []
            row["cited"] = _counts(cited)
            if cited:
                # a verdict that cites this very section is the best sample there is
                row["samples"] = _samples(cited)
            bindings.append(row)
        out.append({"regime": regime.regime, "summary": _summary(bindings), "bindings": bindings})
    return out


def build_report(*, packs: dict[str, FrameworkPack], overlay: Overlay, verdict_rows: list[dict[str, Any]],
                 since: int, facts: dict[str, Any], only: str = "") -> dict[str, Any]:
    by_check: dict[str, list[dict[str, Any]]] = {}
    for r in verdict_rows:
        by_check.setdefault(str(r.get("check_id") or ""), []).append(r)

    wanted = [f for f in (overlay.frameworks or tuple(packs)) if f in packs]
    if only:
        wanted = [f for f in wanted if f == only.lower()]
    frameworks = [_framework_report(packs[f], overlay, by_check, verdict_rows, facts) for f in wanted]
    return {
        "configured": overlay.configured,
        "overlay": {
            "path": overlay.path or "(not configured)",
            "deployment": overlay.deployment,
            "policy_document": overlay.policy_document,
            "frameworks": list(overlay.frameworks),
            "data_classes": {k: len(v) for k, v in overlay.data_classes.items()},
            "unknown_frameworks": [f for f in overlay.frameworks if f not in packs],
        },
        "packs": [{"framework": p.framework, "title": p.title, "version": p.version, "controls": len(p.controls)}
                  for p in packs.values()],
        "window": {"since": since, "verdict_rows": len(verdict_rows), "truncated": bool(facts.get("truncated"))},
        "frameworks": frameworks,
        "domain_regime": _regime_report(overlay, by_check, verdict_rows, facts),
        "facts": {k: v for k, v in facts.items() if k != "truncated"},
        "states": list(STATES),
    }
