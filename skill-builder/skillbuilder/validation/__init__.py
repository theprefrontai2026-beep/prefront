"""Validation engine — the trust layer.

`run_all` runs every validator over a candidate-rule set and returns a
:class:`ValidationReport` plus the :class:`UnresolvedItem`s the validators raise.
The executability validator is the load-bearing one: it mirrors the runtime
binder's four-namespace resolution (`semantic-layer/.../policybind.py`) at design
time, so a rule that would be rejected at publish is caught here instead.
"""

from .engine import run_all

__all__ = ["run_all"]
