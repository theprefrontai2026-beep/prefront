"""The check REGISTRY, and which checks this deployment has enabled.

Two things live here, and nothing else:

1. **`REGISTRY`** - every check id the engine can emit, with the family it
   belongs to and a one-line description. Until now the closest thing to
   this list was `compliance/classes.py`'s `FAMILY_OF_CHECK`, which existed
   only to answer a compliance question; that mapping is now DERIVED from
   this table (`classes.py` imports it), so there is one list, not two that
   can drift. `tests/test_compliance.py` already asserts the mapping agrees
   with the modules that actually emit the ids, and `tests/test_checks.py`
   asserts this table is exactly those ids - both directions, same as before.

2. **`CheckSettings`** - the enabled/disabled set, a per-deployment artifact
   in the same sense as the rule pack or the intent catalog: loaded at
   startup, versioned, passed EXPLICITLY into evaluation (never read from a
   global there, so `evaluate_session` stays a pure function of its
   arguments - Hard Rule 12's replayability depends on that).

Why the disabled set is versioned: it changes which verdicts a session
produces, so it has to change the evaluation version key, or an already-
evaluated session would keep its old verdicts forever
(`store.is_evaluated`). Toggling a check therefore re-evaluates the
sessions the worker can still see - which is what makes re-enabling a check
restore its verdicts for sessions evaluated while it was off, rather than
leaving a permanent hole.

It is deliberately NOT part of `VersionStamp`. That records what produced
the CONTENT of a verdict (Hard Rule 11); enablement only decides whether a
verdict is emitted at all, and adding it would mean a new persisted column
whose value is identical on every row of a given evaluation pass.

Domain independence: a check id is engine vocabulary. Nothing in this file
names a table, column, role or deployment (Hard Rule 1) - and the
descriptions are string literals, so they are held to the domain-noun guard
too, not just the deployment-name one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

from .contract import family_label


@dataclass(frozen=True)
class CheckInfo:
    """One check, as a person configuring the engine sees it."""

    check_id: str
    family: str
    title: str
    detail: str
    # Population checks are computed on demand over MANY sessions
    # (`evaluate_population`, POST /eval/population), not during a session's
    # own evaluation - worth saying out loud in a settings UI, where the
    # other 27 all run on every session.
    population: bool = False


REGISTRY: tuple[CheckInfo, ...] = (
    # --- Family 1: the customer's own extracted, approved rules --------------
    # These ids are produced per RULE (`family1/compilepack.py`'s
    # `Rule.check_id()`), so a deployment with no rule pack emits none of
    # them - disabling one here is still meaningful: it silences that rule
    # SHAPE across every rule of that engine.
    CheckInfo("precondition", "family1", "Precondition",
              "A required earlier step ran before the step that depends on it."),
    CheckInfo("sequencing", "family1", "Sequencing",
              "Steps happened in the order the rule requires."),
    CheckInfo("prohibition", "family1", "Prohibition",
              "A call the rule forbids, given the values observed on it."),
    CheckInfo("field_restriction", "family1", "Field restriction",
              "A restricted field was not repeated in what the agent said."),
    CheckInfo("approval_gate", "family1", "Approval gate",
              "A gated action carried approval from a permitted approver."),
    CheckInfo("substitution", "family1", "Substitution",
              "A value the rule says to replace was replaced in the answer."),

    # --- Family 2: built-in integrity invariants, no artifacts needed --------
    CheckInfo("param_provenance", "family2", "Argument provenance",
              "Every argument traces back to a value this session actually saw."),
    CheckInfo("param_mutation", "family2", "Argument mutation",
              "An argument was not altered beyond the allowed rounding tolerance."),
    CheckInfo("param_discard", "family2", "Discarded value",
              "A value the agent had just retrieved was used, not silently dropped."),
    CheckInfo("param_taint", "family2", "Injection resistance",
              "Retrieved content never became a privileged argument."),
    CheckInfo("param_staleness", "family2", "Argument staleness",
              "An argument still matched its source when it was used."),
    CheckInfo("entity_consistency", "family2", "Subject consistency",
              "A step's subject matches the step its arguments came from."),
    CheckInfo("result_fidelity", "family2", "Answer fidelity",
              "Claims in the answer are grounded in what the tools returned."),
    CheckInfo("error_blindness", "family2", "Error blindness",
              "A failed call was not answered as though it had succeeded."),
    CheckInfo("approval_evidence", "family2", "Approval evidence",
              "A gated action carries real evidence of approval, not a claim of one."),
    CheckInfo("minimization", "family2", "Data minimization",
              "No more rows were pulled back than the work required."),

    # --- Family 3: behaviour vs the published intent catalog -----------------
    CheckInfo("catalog_membership", "family3", "Catalog membership",
              "The operation invoked is one the catalog declares."),
    CheckInfo("entitlement", "family3", "Entitlement",
              "The caller's role is one the intent permits."),
    CheckInfo("version_conformance", "family3", "Version conformance",
              "The call matches the published version of its intent."),
    CheckInfo("side_effect_class", "family3", "Side-effect class",
              "A state-changing call was declared as one."),
    CheckInfo("field_scope", "family3", "Field scope",
              "Returned fields stay inside the intent's approved set."),
    CheckInfo("filter_scope", "family3", "Filter scope",
              "A filter the intent makes mandatory was actually supplied."),
    CheckInfo("volume_scope", "family3", "Volume scope",
              "Rows returned stay inside the intent's declared volume."),
    CheckInfo("toxic_combination", "family3", "Toxic combination",
              "Separately-permitted intents were not combined into a forbidden pair."),
    CheckInfo("goal_alignment", "family3", "Goal alignment",
              "The work done matches what the user actually asked for."),
    CheckInfo("workflow_integrity", "family3", "Workflow integrity",
              "A closing obligation the intent declares was carried out."),
    CheckInfo("redundancy", "family3", "Redundancy",
              "The same call was not repeated with nothing new to learn."),
    CheckInfo("outcome_consistency", "family3", "Outcome consistency",
              "Repeats of one scenario reached the same outcome.", population=True),
    CheckInfo("invocation_drift", "family3", "Invocation drift",
              "Two variants of an agent invoked the same operations.", population=True),
    CheckInfo("verdict_trend", "family3", "Verdict trend",
              "A rule's violation rate has not shifted over time.", population=True),
)

BY_ID: dict[str, CheckInfo] = {c.check_id: c for c in REGISTRY}
ALL_IDS: frozenset[str] = frozenset(BY_ID)

# The canonical check -> family mapping. `compliance/classes.py` re-exports
# this rather than keeping its own copy.
FAMILY_OF_CHECK: dict[str, str] = {c.check_id: c.family for c in REGISTRY}

FAMILIES: tuple[str, ...] = ("family1", "family2", "family3")


def by_family(family: str) -> tuple[CheckInfo, ...]:
    return tuple(c for c in REGISTRY if c.family == family)


# --- the enabled/disabled artifact ------------------------------------------

# The version string a deployment with everything on reports. A word rather
# than a hash so the common case reads plainly in a version key and in
# /eval/status.
DEFAULT_VERSION = "all"


@dataclass(frozen=True)
class CheckSettings:
    """Which checks this deployment has turned off. Frozen, like every other
    artifact the evaluator consumes - a settings change produces a NEW value
    that the caller passes in, it never mutates one already in flight."""

    disabled: frozenset[str] = frozenset()

    @property
    def version(self) -> str:
        """Stable across restarts and across processes: a hash of the sorted
        ids, so the same disabled set always produces the same version key
        (Hard Rule 12 - replay must be a no-op, and `is_evaluated` compares
        this string)."""
        if not self.disabled:
            return DEFAULT_VERSION
        payload = ",".join(sorted(self.disabled))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]

    def enabled(self, check_id: str) -> bool:
        return check_id not in self.disabled

    def keep(self, verdicts: Iterable) -> list:
        """Drop verdicts from disabled checks.

        The gate lives HERE, in the orchestration layer, rather than inside
        each family: the three families expose check ids at three different
        granularities (Family 2 one per module, Family 1 one per rule via
        `Rule.check_id()`, Family 3 several per function), so a per-family
        gate would be three different mechanisms to keep in step. Filtering
        the emitted verdicts is one mechanism, applies uniformly, and keeps
        every check a pure function that knows nothing about settings
        (Hard Rule 3)."""
        if not self.disabled:
            return list(verdicts)
        return [v for v in verdicts if v.check_id not in self.disabled]

    def to_json(self) -> str:
        return json.dumps({"disabled": sorted(self.disabled)}, separators=(",", ":"))

    @staticmethod
    def from_ids(ids: Iterable[str]) -> "CheckSettings":
        """Unknown ids are dropped, not rejected: the stored set outlives the
        build that wrote it, and a check removed in a later version must not
        make the settings unloadable (Hard Rule 9's spirit - a configuration
        gap degrades, it does not error)."""
        return CheckSettings(frozenset(i for i in ids if i in ALL_IDS))

    @staticmethod
    def from_json(text: str) -> "CheckSettings":
        if not text:
            return CheckSettings()
        try:
            raw = json.loads(text)
        except (ValueError, TypeError):
            return CheckSettings()
        ids = raw.get("disabled") if isinstance(raw, dict) else raw
        if not isinstance(ids, list):
            return CheckSettings()
        return CheckSettings.from_ids(str(i) for i in ids)


EMPTY = CheckSettings()

# The process's current settings. One holder, set once at startup from the
# store and again on every write, so the read path (ch.py's filters) and the
# worker see the same value without threading it through every signature.
# Evaluation still takes it as an explicit ARGUMENT - this is the place the
# API and the worker read it FROM, not a back channel into a check.
_current: CheckSettings = EMPTY


def current() -> CheckSettings:
    return _current


def set_current(settings: CheckSettings) -> CheckSettings:
    global _current
    _current = settings
    return _current


def describe(settings: CheckSettings | None = None,
             configured: dict[str, bool] | None = None) -> dict:
    """The registry + enablement as the settings UI consumes it, grouped by
    family. `configured` says whether each family's artifact is present
    (Family 1 needs a rule pack, Family 3 a catalog, Family 2 never does) so
    the UI can distinguish "off because you turned it off" from "idle
    because nothing is configured" - two very different states that would
    otherwise both render as a silent check."""
    s = settings if settings is not None else current()
    conf = configured or {}
    return {
        "families": [
            {
                "family": fam,
                "label": family_label(fam),
                "configured": conf.get(fam, True),
                "checks": [
                    {
                        "check_id": c.check_id, "title": c.title, "detail": c.detail,
                        "population": c.population, "enabled": s.enabled(c.check_id),
                    }
                    for c in by_family(fam)
                ],
            }
            for fam in FAMILIES
        ],
        "disabled": sorted(s.disabled),
        "version": s.version,
        "total": len(REGISTRY),
        "enabled": len(REGISTRY) - len(s.disabled),
    }
