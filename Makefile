# Prefront — a few common developer/CI entry points. Per-service commands
# (uv venvs, per-package tests) are documented in CLAUDE.md; this file only
# wires the ones worth a one-word invocation.

.PHONY: grade-loanpro regress-securebank regress test

# autonomous_build.md step 15: the acceptance gate for eval-engine Phases A-B.
# Needs the engine's compose up (docker compose up --build) AND LoanPro's own
# (docker compose -f loanpro-demo/docker-compose.yml up --build -d - see that
# file's header comment for why it's separate) with an LLM key configured,
# since most scenarios run mode:"llm" turns against the real ungoverned
# agent. Exits non-zero on any FAIL grade. Deliberately NOT in
# `.github/workflows/tests.yml` for the same reason it isn't in `test`
# below - it needs the live stack + a metered LLM key, neither of which a
# plain CI runner has; wire it in once one does.
grade-loanpro:
	cd loanpro-demo && python3 grading_harness.py --out docs/eval-coverage.md --json-out docs/eval-coverage.json

# The INLINE counterpart to grade-loanpro. The two demos exercise opposite
# halves of Prefront and had very different coverage: LoanPro's out-of-band
# path has had a graded harness since step 15, while SecureBank's in-band path
# — where Prefront actually blocks, masks and routes for approval — had no
# regression at all, so a governed decision could change and nothing would
# notice. Needs the engine compose up AND securebank-demo's, with an LLM key.
# Out of CI for the same reason grade-loanpro is: live stack + metered key.
regress-securebank:
	cd securebank-demo && python3 inline_regression.py --json-out docs/inline-regression.json

# Both live regressions: LoanPro out-of-band, SecureBank inline. Neither is in
# `test` below, which is the offline suite.
regress: grade-loanpro regress-securebank

# autonomous_build.md step 15/20: every OFFLINE (no Docker, no LLM key)
# Python test suite in this repo, run from each service's own venv - the
# same set `.github/workflows/tests.yml` runs in CI. Assumes each service's
# venv already exists (see CLAUDE.md's "Per-package dev" section); this
# target doesn't create them, since a fresh `uv pip install` per run is slow
# and CI creates its own via actions/setup-python instead.
test:
	cd eval-engine && VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q
	cd skill-builder && VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q
	cd semantic-mcp-server && VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q
	cd semantic-layer && VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q
	cd skill-builder && VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q \
		../loanpro-demo/test_grading_harness.py ../loanpro-demo/test_preflight_import.py
	sh eval-engine/sync.sh --check
