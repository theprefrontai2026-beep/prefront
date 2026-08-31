"""Compliance reporting: framework packs (Layer A) x deployment overlay (Layer B)
joined over the verdicts the engine already produced. See compliance_design.md.

Nothing here evaluates anything. A control is a VIEW over existing verdicts:
`classes.py` fixes which check ids evidence which control class (engine
vocabulary only), `packs.py` loads the shipped framework packs, `overlay.py`
loads the per-deployment overlay that binds abstract data classes to real
columns, and `report.py` folds verdict rows into per-control states with the
three honest outcomes - evidenced / violated / no evidence.
"""

from .classes import CONTROL_CLASS_CHECKS, CONTROL_CLASSES, FIELD_AWARE_CHECKS
from .overlay import EMPTY_OVERLAY, Overlay, load_overlay
from .packs import Control, FrameworkPack, load_packs
from .report import build_report

__all__ = [
    "CONTROL_CLASS_CHECKS", "CONTROL_CLASSES", "FIELD_AWARE_CHECKS",
    "EMPTY_OVERLAY", "Overlay", "load_overlay",
    "Control", "FrameworkPack", "load_packs",
    "build_report",
]
