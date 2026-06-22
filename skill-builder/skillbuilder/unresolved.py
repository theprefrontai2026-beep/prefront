"""Unresolved-item helpers.

Thin layer over the store for first-class unresolved items. The items themselves
are built by the validation engine (`validation/engine.py`) as
:class:`~skillbuilder.schema.UnresolvedItem` models; this module persists them and
answers list/resolve/waive.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .schema import UnresolvedItem


def save(store, document_id: str, items: Iterable[UnresolvedItem]) -> int:
    return store.replace_unresolved_items(document_id, list(items))


def list_items(store, document_id: Optional[str] = None) -> list[dict]:
    return store.list_unresolved_items(document_id)


def resolve(
    store,
    unresolved_id: str,
    *,
    status: str = "resolved",
    resolved_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    if status not in ("resolved", "waived", "open"):
        raise ValueError(f"invalid status {status!r}")
    return store.resolve_unresolved_item(
        unresolved_id, status=status, resolved_by=resolved_by, notes=notes
    )


def has_open_critical(store, document_id: str) -> bool:
    """True if any open critical unresolved item blocks publication."""
    for row in store.list_unresolved_items(document_id):
        item = row.get("item") or {}
        if row.get("status") == "open" and (
            item.get("severity") == "critical" or item.get("blocks_publication")
        ):
            return True
    return False
