"""`warrant/` names no deployment, and this test is what keeps it that way.

The repo's defining principle is that the engine is pure mechanism: no table,
column, role, tenant or threshold from any demo may appear in engine code. The
evaluation engine has enforced this with a test since it was built
(`eval-engine/tests/test_domain_independence.py`); the enforcement plane is new
engine code and gets the same guard from the start rather than acquiring one
later, once violations are already load-bearing.

Scope is deliberately the one the eval-engine guard uses and can actually
defend: DEPLOYMENT NAMES, anywhere in the package including comments. A demo
name in a comment is the beginning of a demo name in a branch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "warrant"

# Every deployment that ships in this repo. A new demo directory must be added
# here in the same change that creates it.
DEPLOYMENT_NAMES = ("securebank", "loanpro", "prefront-ui")


def python_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_the_package_has_files_to_check():
    """Guards the guard: an rglob that silently matched nothing would make
    every assertion below vacuously true."""
    assert len(python_files()) >= 8


@pytest.mark.parametrize("name", DEPLOYMENT_NAMES)
def test_no_deployment_is_named_anywhere_in_the_package(name):
    pattern = re.compile(re.escape(name), re.IGNORECASE)
    offenders = [
        f"{path.relative_to(PACKAGE)}:{i}"
        for path in python_files()
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        f"deployment name {name!r} appears in engine code at {offenders}. "
        "The enforcement plane names no deployment: a customer's action "
        "classes, resources and counterparties reach it through the Mission "
        "and the action registry, never through code"
    )


def test_no_action_class_vocabulary_is_hardcoded():
    """The registry ships empty. A default verb list would be this package
    having an opinion about a customer's tool surface."""
    from warrant import ActionRegistry

    assert len(ActionRegistry()) == 0
