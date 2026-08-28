"""Hard Rule 1: the engine names no demo. grep -rin "loanpro|securebank"
eval-engine/evalengine == 0 hits, forever. This is the CI/pytest guard
autonomous_build.md step 2 calls for.
"""

from __future__ import annotations

import pathlib
import re

FORBIDDEN = re.compile(r"loanpro|securebank", re.IGNORECASE)
ENGINE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "evalengine"


def test_no_demo_vocabulary_in_engine_code():
    hits = []
    for path in ENGINE_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in (".py", ".yaml", ".yml"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(line):
                hits.append(f"{path.relative_to(ENGINE_ROOT.parent)}:{lineno}: {line.strip()}")
    assert not hits, "engine core must name no demo:\n" + "\n".join(hits)
