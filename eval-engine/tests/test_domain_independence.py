"""Hard Rule 1: the engine names no demo, and names no demo's DOMAIN.

Two guards, deliberately different in strictness:

1. `test_no_demo_vocabulary_in_engine_code` - the deployment names
   ("loanpro", "securebank") may not appear ANYWHERE in `evalengine/`, comments
   and YAML included. This is the guard `autonomous_build.md` step 2 calls for.
2. `test_no_domain_nouns_in_executable_engine_code` - a demo's business
   vocabulary (loan, applicant, credit score, account, teller, ...) may not
   appear in EXECUTABLE code: identifiers, string literals, dict keys. Comments
   and docstrings are exempt on purpose - several modules explain a general
   mechanism by way of a concrete loan example (`family2/entity_consistency.py`,
   `family1/facts.py`), and that documentation is worth keeping. What must stay
   domain-free is anything the engine can BRANCH on.

Guard 2 exists because guard 1 was weaker than the principle it was named for:
it passes cleanly on an engine that hardcodes `credit_score` or `applicant_id`,
since neither string contains a deployment name. Every domain noun below probed
to zero hits against `evalengine/` when this was written, so the list needs no
exemptions - if one ever becomes genuinely generic engine vocabulary, RENAME
the engine's use of it rather than deleting the word here, or argue the
exemption explicitly in the diff.
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import tokenize

FORBIDDEN = re.compile(r"loanpro|securebank", re.IGNORECASE)
ENGINE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "evalengine"

# A demo's business vocabulary. Drawn from both in-repo demos (LoanPro's
# lending domain, SecureBank's retail-banking one) plus the neighbouring nouns
# a third demo would plausibly bring, so the guard bites before a new domain
# is onboarded rather than after.
DOMAIN_NOUNS = frozenset("""
    loan applicant borrower underwriter underwriting officer mortgage collateral
    subprime superprime prime tier bureau kyc credit score risk affordability
    disbursement delinquency escrow income salary wage employer

    account transaction balance customer branch teller iban routing merchant
    payee deposit withdrawal card pin ssn taxid
""".split())

# Identifiers carry domain vocabulary as word PARTS ("credit_score",
# "applicantId"), so tokens are split before matching. Matching the raw token
# against a substring would flag "frontier" for "tier" and "payload" for "loan".
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
_NON_ALPHA = re.compile(r"[^A-Za-z]+")

# f-string text is FSTRING_MIDDLE on 3.12+, plain STRING before it.
_TEXT_TOKENS = tuple(
    t for t in (tokenize.NAME, tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", None))
    if t is not None
)


def _words(token_text: str) -> set[str]:
    normalized = _CAMEL_BOUNDARY.sub("_", token_text)
    return {w.lower() for w in _NON_ALPHA.split(normalized) if w}


def _docstring_positions(source: str) -> set[tuple[int, int]]:
    """(line, col) of every module/class/function docstring node.

    Positions rather than "is this string the first statement?" token
    heuristics: the heuristic has to special-case module docstrings, decorated
    defs and `if:`-bodies, and gets one of them wrong quietly - which is the
    exact failure mode this whole file exists to prevent.
    """
    tree = ast.parse(source)
    out: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            out.add((first.value.lineno, first.value.col_offset))
    return out


def domain_hits(source: str, label: str = "<source>") -> list[str]:
    """Domain nouns appearing in executable code. Comments/docstrings exempt."""
    docstrings = _docstring_positions(source)
    hits: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type not in _TEXT_TOKENS:
            continue  # COMMENT is excluded here
        if tok.type == tokenize.STRING and tok.start in docstrings:
            continue
        found = _words(tok.string) & DOMAIN_NOUNS
        if found:
            hits.append(f"{label}:{tok.start[0]}: {sorted(found)} in {tok.string[:60]!r}")
    return hits


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


def test_no_domain_nouns_in_executable_engine_code():
    hits = []
    for path in sorted(ENGINE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        hits += domain_hits(source, str(path.relative_to(ENGINE_ROOT.parent)))
    assert not hits, (
        "engine core must name no demo's domain (comments/docstrings exempt):\n"
        + "\n".join(hits)
    )


# --- the guard's own positive controls -------------------------------------
# A detector with no test that it can FIRE is the failure mode this repo has
# hit repeatedly in the checks themselves (see eval-engine/CLAUDE.md's bug
# table). These keep the scanner from silently degrading into a no-op.

def test_guard_catches_a_domain_noun_in_an_identifier():
    assert domain_hits("def f(row):\n    return row['credit_score']\n")


def test_guard_catches_a_domain_noun_in_a_string_literal():
    assert domain_hits('def f():\n    return "applicant"\n')


def test_guard_catches_a_domain_noun_in_an_fstring():
    assert domain_hits('def f(x):\n    return f"loan {x} rejected"\n')


def test_guard_exempts_comments_and_docstrings():
    source = (
        '"""Module docstring mentioning a loan and an applicant."""\n'
        "\n"
        "def f(x):\n"
        '    """Explains the mechanism via a credit_score example."""\n'
        "    # an applicant's account balance, said in a comment\n"
        "    return x\n"
    )
    assert domain_hits(source) == []


def test_guard_does_not_flag_a_word_that_merely_contains_a_domain_noun():
    # "frontier" contains "tier"; "payload" contains "loa"+"d". Both are real
    # words in this engine (family1/facts.py walks a `frontier`).
    assert domain_hits("def f():\n    frontier = payload = 1\n    return frontier, payload\n") == []
