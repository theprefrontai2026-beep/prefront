"""Prefront governance layer — business-policy enforcement (PDP) for the MCP runtime.

A modular pipeline of small stages, each with one job:

    identity  resolve the trusted caller (role/region) — never from agent args
    authz     caller role vs the intent's allowed_roles (from allow-rules)
    facts     decision inputs: precheck row ∪ request args ∪ caller ∪ metrics
    rules     evaluate the published, vocabulary-bound rules against the facts
    decide    precedence: block > approval_required > allow (+ field masking)
    trace     append-only decision trace (audit)

Deliberately NOT here (future modules, same Stage shape): token/authN
validation, external policy engines (OPA), persisted approval workflow.
"""

from .context import Caller, Decision, RuleOutcome
from .identity import resolve_caller
from .pipeline import govern
from .rules import PolicyRegistry

__all__ = ["Caller", "Decision", "RuleOutcome", "PolicyRegistry", "govern", "resolve_caller"]
