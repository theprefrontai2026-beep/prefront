"""`warrant/` names no deployment, and this test is what keeps it that way.

The repo's defining principle is that the engine is pure mechanism: no table,
column, role, tenant or threshold from any demo may appear in engine code. The
evaluation engine has enforced this with a test since it was built
(`eval-engine/tests/test_domain_independence.py`); the enforcement plane is new
engine code and gets the same guard from the start rather than acquiring one
later, once violations are already load-bearing.

Two scopes, mirroring that guard:

1. **Deployment names, anywhere**, comments included. A demo name in a comment
   is the beginning of a demo name in a branch.
2. **Business nouns on executable lines.** Prose may say "a vendor nobody
   approved" while explaining what a counterparty is for; executable code may
   not, because the moment it does, the engine has an opinion about one
   application's domain.

The second scope exists because it caught a real regression. A `python -m
warrant demo` command briefly lived in this package and invented a business
vocabulary to demonstrate itself with — harmless-looking, and precisely the
thing that stops an engine being reusable across applications. The demo now
lives in `warrant-demo/`, which is where a deployment's vocabulary belongs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "warrant"

# Every deployment that ships in this repo. A new demo directory must be added
# here in the same change that creates it.
DEPLOYMENT_NAMES = ("securebank", "loanpro", "prefront-ui", "warrant-demo")

# Business nouns no ENGINE should name in executable code. Each is a thing an
# application has, not a thing a mechanism has. Kept short and concrete: a long
# speculative list produces false positives, and a guard with false positives
# gets weakened, which is worse than a short one that is actually enforced.
DOMAIN_NOUNS = (
    "invoice", "vendor", "payment", "payroll", "customer", "patient",
    "iban", "ssn", "loan", "applicant", "claim", "ledger",
)


def python_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def executable_lines(path: Path) -> list[tuple[int, str]]:
    """Lines outside docstrings and comments.

    Deliberately a simple scanner rather than an AST walk. This guard's value
    is that it is obvious enough to trust at a glance, and its failure mode is
    a false positive a human resolves in one second.
    """
    out: list[tuple[int, str]] = []
    in_doc = False
    for number, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.strip()
        fences = stripped.count('"""') + stripped.count("'''")
        if in_doc:
            if fences:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            # A one-line docstring opens and closes on the same line; only an
            # unbalanced fence puts us inside a multi-line one.
            if fences == 1:
                in_doc = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        out.append((number, line.split("#", 1)[0]))
    return out


def test_the_package_has_files_to_check():
    """Guards the guard: an rglob that silently matched nothing would make
    every assertion below vacuously true."""
    assert len(python_files()) >= 8


def test_the_scanner_actually_separates_prose_from_code(tmp_path):
    """Guards the guard, again. A scanner that classified everything as prose
    would make the noun check pass forever."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""A docstring mentioning an invoice."""\n'
        "# A comment mentioning a vendor.\n"
        "x = 1  # trailing comment mentioning a payment\n"
        "y = 'invoice'\n"
    )
    found = executable_lines(sample)
    assert [n for n, _ in found] == [3, 4]
    assert "payment" not in found[0][1]   # trailing comment stripped
    assert "invoice" in found[1][1]       # a real string literal is caught


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
        "The enforcement plane names no deployment: an application's action "
        "classes, resources and counterparties reach it through the Mission "
        "and the action registry, never through code"
    )


@pytest.mark.parametrize("noun", DOMAIN_NOUNS)
def test_no_business_noun_appears_in_executable_code(noun):
    pattern = re.compile(rf"\b{re.escape(noun)}s?\b", re.IGNORECASE)
    offenders = [
        f"{path.relative_to(PACKAGE)}:{i}"
        for path in python_files()
        for i, line in executable_lines(path)
        if pattern.search(line)
    ]
    assert not offenders, (
        f"business noun {noun!r} appears in executable engine code at "
        f"{offenders}. The enforcement plane is mechanism; an application's "
        "vocabulary reaches it through the Mission and the action registry. "
        "If this is demonstration code, it belongs in warrant-demo/"
    )


def test_no_action_class_vocabulary_is_hardcoded():
    """The registry ships empty. A default verb list would be this package
    having an opinion about an application's tool surface."""
    from warrant import ActionRegistry

    assert len(ActionRegistry()) == 0
