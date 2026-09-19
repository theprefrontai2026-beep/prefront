"""The action-class registry: the deployment's verb list and what each one costs.

Prefront's defining principle is that the engine names no domain, and this is
the seam where a customer's own vocabulary enters the enforcement plane. The
registry holds THEIR action classes — whatever `<noun>.<verb>` strings their
tool surface uses — and nothing in this package ships a default list.

Two properties per class, and the PDS uses each for exactly one thing:

  `blast_radius`  feeds the prompt-injection tripwire, which denies HIGH
                  blast-radius actions whose evidence chain includes
                  untrusted-origin content. Low-radius actions are left alone,
                  because a tripwire that fires on reads would stop an agent
                  from browsing the web at all, and the spec is explicit that
                  this product "does not shape or filter what the agent sees".

  `side_effect`   whether a call changes the world. The spec's current design
                  requires attestations on side effects only, and an
                  unregistered class must therefore not be able to slip through
                  as a read.

An UNKNOWN action class is the case worth getting right. It is refused, loudly,
rather than defaulted: the safe-looking default (treat it as low-radius and
read-only) is exactly how a new destructive tool ships on a Friday and is
governed by nothing. An unknown verb means the registry is stale, which is a
deploy-time problem with a deploy-time fix — the spec's lifecycle loop
re-derives the boundary "whenever its tools, prompt or configuration change"
precisely so this stays true.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .contract import BlastRadius, ContractError


@dataclass(frozen=True)
class ActionClass:
    """One entry in the deployment's own verb list."""

    name: str
    blast_radius: BlastRadius = "low"
    side_effect: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ContractError("ActionClass.name is required")
        if self.blast_radius not in ("low", "medium", "high"):
            raise ContractError(
                f"ActionClass.blast_radius must be low|medium|high, got "
                f"{self.blast_radius!r}"
            )


class UnknownActionClass(ContractError):
    """An action class the registry has never heard of.

    Its own type so a gateway can distinguish "your registry is stale" — an
    operational alert with a clear owner — from "that call was not permitted",
    which is a decision about the agent. Reporting the first as the second is
    how a deploy problem gets misfiled as an agent problem for a week.
    """


class ActionRegistry:
    """Immutable lookup over the deployment's action classes.

    Immutable after construction on purpose. The PDS must be a pure function of
    its inputs so a proposed policy can be replayed against recorded
    attestations, and a registry that could change under a replay would make
    two runs of the same evidence disagree.
    """

    def __init__(self, classes: Iterable[ActionClass] = (), version: str = "") -> None:
        self._classes: dict[str, ActionClass] = {}
        for item in classes:
            if item.name in self._classes:
                raise ContractError(f"duplicate action class {item.name!r} in registry")
            self._classes[item.name] = item
        # Stamped onto decisions so one can be replayed against the vocabulary
        # that was live when it was made.
        self.version = version

    def get(self, name: str) -> ActionClass:
        item = self._classes.get(name)
        if item is None:
            raise UnknownActionClass(
                f"action class {name!r} is not in the registry (version "
                f"{self.version or 'unversioned'}). Refusing to assume it is a "
                "harmless read: an unregistered verb means the boundary was not "
                "re-derived after the tool surface changed"
            )
        return item

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._classes))

    def __len__(self) -> int:
        return len(self._classes)

    def __contains__(self, name: object) -> bool:
        return name in self._classes
