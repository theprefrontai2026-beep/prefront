"""Turn approved mined candidates into a real intent_catalog.yaml.

The last step of intent_learning_design.md's L3, and the one that makes the
rest of the mining path worth anything: until a candidate can be approved and
PUBLISHED, everything upstream is a report nobody can act on.

Two properties this module exists to guarantee.

APPROVAL IS THE MOMENT OBSERVATION BECOMES PERMISSION. Everywhere upstream the
vocabulary is observational, because while learning that is what is true: these
are the callers who DID run this. Publishing inverts it — the observed callers
become the `allowed_callers` a runtime will enforce, and a caller nobody
intended to bless is blessed silently. So `approved_roles` is explicit on every
entry and defaults to nothing: a caller reaches the published catalog only
because a human named it, never because the traces contained it.

WHAT IS PUBLISHED IS THE COUNTED HALF, NEVER THE MODEL'S. An inferred policy
sentence is a reading for a human; it is not a fact, and it has no business in
an artifact the runtime enforces. It rides along as a `note` on the entry —
visible to whoever reads the file, load-bearing to nothing.

The output is the SAME artifact the hand-authored path produces, so a learned
catalog is held to the identical Family 3 gate: same schema, same validator,
same loader. Nothing about the runtime knows or cares where a catalog came
from, which is the only way this stays honest.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from .intent_catalog import (
    AllowedCallers,
    ExpectedVolume,
    IntentCatalog,
    IntentCatalogEntry,
    dump_intent_catalog,
    validate_intent_catalog,
)


class ApprovedIntent(BaseModel):
    """One candidate a human has said yes to, with the narrowing they applied."""
    intent: str
    steps: list[str] = Field(default_factory=list)
    tool_name: str = ""
    params: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    side_effect: str = "read"
    # Deliberately NOT defaulted from the observed set. See the module
    # docstring: an empty approved set publishes an entry nobody may call,
    # which is a safe artifact; an implicit one publishes a grant nobody made.
    approved_roles: list[str] = Field(default_factory=list)
    approved_channels: list[str] = Field(default_factory=list)
    mandatory_filters: list[str] = Field(default_factory=list)
    closing_obligation: Optional[str] = None
    expected_rows_p99: Optional[int] = None
    # The model's reading, carried for the reader and enforced by nothing.
    note: str = ""


def to_entry(a: ApprovedIntent) -> IntentCatalogEntry:
    """One approved candidate as a catalog entry.

    `tool_name` falls back to the LAST step, because for a multi-call workflow
    the operation IS its terminal act — the earlier steps are the evidence
    gathered for it, and they become the closing/precondition structure rather
    than the entry's identity.
    """
    tool = a.tool_name or (a.steps[-1] if a.steps else a.intent)
    return IntentCatalogEntry(
        intent=a.intent,
        tool_name=tool,
        params=list(a.params),
        side_effect="write" if a.side_effect not in ("", "read") else "read",
        allowed_callers=AllowedCallers(
            roles=list(a.approved_roles),
            channels=list(a.approved_channels),
        ),
        fields=list(a.fields),
        mandatory_filters=list(a.mandatory_filters),
        expected_volume=(ExpectedVolume(p99=a.expected_rows_p99)
                         if a.expected_rows_p99 is not None else ExpectedVolume()),
        closing_obligation=a.closing_obligation or None,
        # A learned catalog cites observed practice, never a clause. Leaving
        # `policy` empty is the honest state and is already legal — Family 2
        # tags carry no source either — but it means a finding from this
        # catalog will render with no citation, which a reviewer should expect.
        policy=[],
    )


def build_from_approved(approved: list[ApprovedIntent], version: int = 1) -> tuple[IntentCatalog, list[str]]:
    """(catalog, problems). Never raises on bad input — the caller decides.

    Duplicate intent names are a real hazard rather than a nuisance: the
    catalog is keyed by intent, so a second entry silently replaces the first
    and the reviewer's approval of the one they saw is quietly discarded.
    Reported, and the later one dropped rather than overwriting.
    """
    seen: set[str] = set()
    entries: list[IntentCatalogEntry] = []
    problems: list[str] = []
    for a in approved:
        name = (a.intent or "").strip()
        if not name:
            problems.append("an approved candidate has no intent name; skipped")
            continue
        if name in seen:
            problems.append(f"duplicate intent {name!r} — keeping the first, dropping the rest")
            continue
        seen.add(name)
        entry = to_entry(a)
        if not entry.allowed_callers.roles:
            # Loud, because it is the difference between "nobody may call this"
            # and "anybody may": Family 3's entitlement check treats an empty
            # role list as unrestricted on some paths, so publishing one by
            # accident is the widest possible grant wearing the narrowest look.
            problems.append(
                f"{name}: no roles approved — this entry grants nothing explicitly, and an empty "
                f"role list is NOT a deny. Approve the callers you intend, or leave the intent out.")
        entries.append(entry)

    catalog = IntentCatalog(version=version, policy_document="", intents=entries)
    problems.extend(validate_intent_catalog(catalog))
    return catalog, problems


def render(catalog: IntentCatalog, source_note: str = "") -> str:
    """The YAML, with a header saying where it came from.

    A catalog mined from behaviour and one compiled from a policy document are
    the same file to the runtime and very different things to a person reading
    it a year later. The header is the only place that distinction survives.
    """
    header = (
        "# intent_catalog.yaml — MINED FROM OBSERVED BEHAVIOUR, then approved by a human.\n"
        "#\n"
        "# Not compiled from a policy document. Every entry describes what was seen\n"
        "# happening and was approved as acceptable; `allowed_callers` is what a\n"
        "# reviewer explicitly permitted, NOT the set of callers observed.\n"
        "#\n"
        "# A finding from this catalog therefore cites observed practice rather than a\n"
        "# clause, and `policy:` is empty on every entry by construction.\n"
    )
    if source_note:
        header += f"# {source_note}\n"
    return header + "#\n" + dump_intent_catalog(catalog)
