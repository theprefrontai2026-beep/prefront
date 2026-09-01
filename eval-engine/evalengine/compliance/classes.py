"""The fixed, engine-side half of the compliance binding rule:

    control -> control_class -> check_ids

A framework pack names a CONTROL CLASS (never a check id, never a column);
this table resolves the class to the check ids that evidence it. Every id
here is one the families actually emit (`family1/compilepack.py`'s
`_DEFAULT_CHECK`, `family2/__init__.py`'s `CHECKS`, `family3/*.py`'s
`CHECK_*`), and the test suite asserts both directions: every id in this
table is a real check, and every real check appears in at least one class.

Three classes have no check behind them (`audit_logging`, `retention`,
`change_management`) - they are evidenced by properties of the STORE rather
than by a verdict, and `report.py` handles them by name. `basis` on the
report row says which kind a control is.
"""

from __future__ import annotations

from ..checks import FAMILY_OF_CHECK as _FAMILY_OF_CHECK

CONTROL_CLASSES: tuple[str, ...] = (
    "access", "minimization", "purpose_limitation", "segregation", "field_protection",
    "integrity", "human_oversight", "injection_resistance", "audit_logging", "retention",
    "change_management", "monitoring",
)

CONTROL_CLASS_CHECKS: dict[str, tuple[str, ...]] = {
    # only sanctioned callers invoke sanctioned operations; a prohibited call
    # shape is an unsanctioned operation, so `prohibition` sits here too
    "access": ("catalog_membership", "entitlement", "version_conformance", "side_effect_class", "prohibition"),
    # no more fields, rows or calls than the task needs
    "minimization": ("field_scope", "filter_scope", "volume_scope", "minimization", "param_discard"),
    # the work done matches the request that occasioned it
    "purpose_limitation": ("goal_alignment",),
    # separately-permitted reads are not composed into an unpermitted whole
    "segregation": ("toxic_combination",),
    # protected fields are masked, withheld, or substituted
    "field_protection": ("field_restriction", "substitution"),
    # values flowing in and out are neither invented nor altered
    "integrity": ("param_provenance", "param_mutation", "param_staleness", "entity_consistency",
                  "result_fidelity", "error_blindness"),
    # gated actions carry real approval / their prerequisite, not a claimed one
    "human_oversight": ("approval_gate", "approval_evidence", "workflow_integrity", "precondition", "sequencing"),
    # retrieved content never becomes a privileged parameter
    "injection_resistance": ("param_taint",),
    # evidenced by the store, not by a check
    "audit_logging": (),
    "retention": (),
    "change_management": (),
    # behaviour watched for anomalies and drift over time
    "monitoring": ("outcome_consistency", "invocation_drift", "verdict_trend", "redundancy"),
}

# Checks whose `detail` names the field(s) they judged, so a data-class
# binding (Layer B) can narrow their verdicts to "on data of this class".
# Every other check is scoped at the check level - the report says which.
FIELD_AWARE_CHECKS: frozenset[str] = frozenset({
    "field_restriction", "substitution", "field_scope", "filter_scope",
})

# Which family a check belongs to - needed to say "not configured" honestly
# (Family 1 needs a rule pack, Family 3 a catalog; Family 2 always runs).
#
# Re-exported from `evalengine.checks`, which is the ONE table of every check
# id the engine can emit. This module used to keep its own copy; two lists of
# the same 30 ids drift the moment a check is added, and only one of them had
# a test. Imported rather than reproduced, so `from .classes import
# FAMILY_OF_CHECK` keeps working for every existing caller and test.
FAMILY_OF_CHECK: dict[str, str] = dict(_FAMILY_OF_CHECK)

# The store-evidenced classes, by name, so report.py and the tests agree.
STORE_BASED_CLASSES: frozenset[str] = frozenset({"audit_logging", "retention", "change_management"})
