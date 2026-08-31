"""Draft a compliance overlay (Layer B of compliance_design.md) from a PII
scan - the design-time half of "close the PII-analyser loop" (§5.3, §6 step 2).

Deterministic, no LLM: the pii-analyzer's column-name recognisers already
say "this column looks like an SSN / a card number / a phone"; this module
maps those Presidio entity labels onto the overlay's abstract data classes
and emits a CANDIDATE overlay. It is never written to the artifacts volume
by this service - a human reviews it, fixes what the name-based guess got
wrong, and publishes it beside the deployment's other artifacts (the same
posture as every other candidate in this repo).

    fields  = [{"table": "t", "column": "c", "entity": "US_SSN"}, ...]
    overlay = suggest_overlay(deployment="acme", policy_document="policy.md", fields=fields)
"""

from __future__ import annotations

from typing import Any, Iterable

import yaml

SCHEMA_VERSION = "prefront.compliance_overlay.v1"

# The overlay's data classes (compliance_design.md §2.1). Order is the order
# they are emitted in, so a reviewer sees the same layout every time.
DATA_CLASSES: tuple[str, ...] = (
    "personal_data", "special_category", "phi", "cardholder_data", "sensitive_auth_data",
    "financial_npi", "credentials", "internal_confidential",
)

# Presidio entity (as pii-analyzer/app.py emits it) -> data class. A column
# can carry more than one: a card number is cardholder data AND personal
# data of the holder. Anything unlisted is reported as `unmapped`, never
# guessed into a class.
ENTITY_CLASSES: dict[str, tuple[str, ...]] = {
    "EMAIL_ADDRESS": ("personal_data",),
    "PHONE_NUMBER": ("personal_data",),
    "US_SSN": ("personal_data",),
    "PERSON": ("personal_data",),
    "DATE_TIME": ("personal_data",),          # the analyser only flags dates of birth
    "IP_ADDRESS": ("personal_data",),
    "LOCATION": ("personal_data",),
    "US_DRIVER_LICENSE": ("personal_data",),
    "US_PASSPORT": ("personal_data",),
    "CREDIT_CARD": ("cardholder_data", "personal_data"),
    "US_BANK_NUMBER": ("financial_npi", "personal_data"),
    "NRP": ("special_category", "personal_data"),
    "MEDICAL_LICENSE": ("phi",),
}


def suggest_overlay(*, deployment: str, policy_document: str = "", fields: Iterable[dict[str, Any]],
                    frameworks: Iterable[str] = ()) -> dict[str, Any]:
    """Return {"overlay": <dict>, "yaml": <str>, "unmapped": [...], "bound": n}."""
    classes: dict[str, list[str]] = {c: [] for c in DATA_CLASSES}
    unmapped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for f in fields:
        table = str(f.get("table") or "").strip()
        column = str(f.get("column") or "").strip()
        entity = str(f.get("entity") or "").strip().upper()
        if not column:
            continue
        ref = f"{table}.{column}" if table else column
        targets = ENTITY_CLASSES.get(entity)
        if not targets:
            unmapped.append({"column": ref, "entity": entity})
            continue
        for cls in targets:
            if (cls, ref) not in seen:
                seen.add((cls, ref))
                classes[cls].append(ref)
    overlay = {
        "schema_version": SCHEMA_VERSION,
        "deployment": deployment,
        "policy_document": policy_document,
        "data_classes": {c: sorted(v) for c, v in classes.items()},
        "frameworks": [str(x).strip().lower() for x in frameworks if str(x).strip()],
        # the deployment's own regime is a human's to write - the scan cannot know it
        "domain_regime": [],
    }
    text = (
        "# CANDIDATE compliance overlay drafted from a column-NAME PII scan.\n"
        "# Review every binding (a name-based guess can be wrong in both\n"
        "# directions), add the deployment's own regime under domain_regime,\n"
        "# then publish beside its other artifacts and point\n"
        "# EVAL_COMPLIANCE_OVERLAY_PATH at it. See compliance_design.md §2.2.\n"
        + yaml.safe_dump(overlay, sort_keys=False, default_flow_style=False)
    )
    return {"overlay": overlay, "yaml": text, "unmapped": unmapped,
            "bound": sum(len(v) for v in classes.values())}
