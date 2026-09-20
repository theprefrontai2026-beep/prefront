"""Regenerate `policy/action_registry.yaml` from `world.ACTIONS`.

    python3 gen_registry.py      (needs pyyaml; warrant-service's venv has it)

The registry is generated rather than hand-maintained so the file the SERVICE
reads and the registry the EMBEDDED demo uses cannot drift. If they did, the
two modes would decide differently for a reason that has nothing to do with the
service — precisely the confusion the parity test exists to rule out.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent / "warrant", HERE.parent / "warrant-service"):
    sys.path.insert(0, str(extra))

import yaml  # noqa: E402

import remote  # noqa: E402

OUT = HERE / "policy" / "action_registry.yaml"
SPLIT = "\nversion:"


def header() -> str:
    """The generated file's own header, preserved across regenerations.

    Read back from the file rather than kept as a literal here, so the prose a
    reader sees and the prose this script writes are the same string — a
    duplicate would drift the first time someone improved one of them.
    """
    if not OUT.exists() or SPLIT not in OUT.read_text():
        raise SystemExit(
            "policy/action_registry.yaml is missing or has lost its header comment. "
            "Restore it from git rather than regenerating without one: the header "
            "is what tells the next reader the file is generated"
        )
    return OUT.read_text().split(SPLIT, 1)[0]


def main() -> int:
    doc = remote.registry_document()
    OUT.write_text(header() + SPLIT + yaml.safe_dump(doc, sort_keys=False).split("version:", 1)[1])
    print(f"policy/action_registry.yaml · {len(doc['actions'])} action classes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
