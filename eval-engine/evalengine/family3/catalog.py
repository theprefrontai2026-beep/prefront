"""Load and validate a published intent_catalog.yaml (autonomous_build.md
§3.4). Family 3 degrades to "not configured" when no catalog is on disk
(Hard Rule 9) - callers get an empty IntentCatalog, never an exception.

Schema matches semantic-layer/semanticlayer/intent_catalog.py field-for-field
(that module generates one from a bound semantic model; a bring-your-own-agent
deployment with no semantic-layer binding authors one by hand instead) - kept
as a second, independent loader here rather than a shared import because
this package has its own Docker build context (same convention as every
other Prefront service pair).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import yaml


@dataclass(frozen=True)
class IntentEntry:
    intent: str
    tool_name: str = ""
    params: tuple[str, ...] = ()
    side_effect: str = "read"
    allowed_roles: tuple[str, ...] = ()
    allowed_channels: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    restricted_fields: tuple[str, ...] = ()
    mandatory_filters: tuple[str, ...] = ()
    expected_rows_p99: Optional[int] = None
    closing_obligation: str = ""
    toxic_with: tuple[str, ...] = ()
    trigger_descriptors: tuple[str, ...] = ()
    trust: str = "trusted"
    policy: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentCatalog:
    version: str = ""
    # Name of the document `IntentEntry.policy` section numbers are section
    # refs INTO - generic (never a hardcoded filename in engine code); a
    # catalog with no policy citations at all can leave this blank.
    policy_document: str = ""
    intents: dict[str, IntentEntry] = field(default_factory=dict)

    def source_for(self, entry: IntentEntry) -> Optional[dict]:
        """The citation block a verdict/tag attaches when this entry
        declares policy sections (Hard Rule 17: never populated otherwise)."""
        if not entry.policy:
            return None
        return {"document": self.policy_document, "section": ", ".join(entry.policy)}


EMPTY = IntentCatalog()


def _entry(raw: dict[str, Any]) -> IntentEntry:
    allowed = raw.get("allowed_callers") or {}
    volume = raw.get("expected_volume") or {}
    return IntentEntry(
        intent=str(raw.get("intent", "")),
        tool_name=str(raw.get("tool_name", "")),
        params=tuple(raw.get("params") or ()),
        side_effect=str(raw.get("side_effect", "read")),
        allowed_roles=tuple(allowed.get("roles") or ()),
        allowed_channels=tuple(allowed.get("channels") or ()),
        fields=tuple(raw.get("fields") or ()),
        restricted_fields=tuple(raw.get("restricted_fields") or ()),
        mandatory_filters=tuple(raw.get("mandatory_filters") or ()),
        expected_rows_p99=volume.get("rows_p99"),
        closing_obligation=str(raw.get("closing_obligation") or ""),
        toxic_with=tuple(raw.get("toxic_with") or ()),
        trigger_descriptors=tuple(raw.get("trigger_descriptors") or ()),
        trust=str(raw.get("trust", "trusted")),
        policy=tuple(raw.get("policy") or ()),
    )


def parse(raw: dict[str, Any]) -> IntentCatalog:
    body = raw["intent_catalog"]
    intents = [_entry(r) for r in (body.get("intents") or [])]
    return IntentCatalog(
        version=str(body.get("version", "1")),
        policy_document=str(body.get("policy_document", "")),
        intents={e.intent: e for e in intents},
    )


def load(path: str = "") -> IntentCatalog:
    if not path:
        return EMPTY
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw:
        return EMPTY
    return parse(raw)
