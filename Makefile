# Prefront — a few common developer/CI entry points. Per-service commands
# (uv venvs, per-package tests) are documented in CLAUDE.md; this file only
# wires the ones worth a one-word invocation.

.PHONY: grade-loanpro

# autonomous_build.md step 15: the acceptance gate for eval-engine Phases A-B.
# Needs the bundled stack up (docker compose up --build) with an LLM key
# configured, since most scenarios run mode:"llm" turns against the real
# ungoverned agent. Exits non-zero on any FAIL grade - wire this into CI once
# a runner with the stack + API key is available.
grade-loanpro:
	cd loanpro-demo && python3 grading_harness.py --out docs/eval-coverage.md --json-out docs/eval-coverage.json
