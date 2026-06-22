"""Document profiler.

Detects the shape of a policy document (numbered sections, definitions table,
approval matrix, thresholds, exceptions, audit text, related-policy references)
and guesses its domain. The LLM pass is preferred; a deterministic heuristic over
the canonical markdown is the fallback so the stage always returns a profile.
"""

from __future__ import annotations

import re
from typing import Optional

from .schema import DocumentProfile, ProfileWarning

SYSTEM_PROMPT = (
    "You are profiling a business policy document for a policy compiler. "
    "Return ONLY JSON. Identify: detected_source_type, detected_domain, "
    "domain_confidence (0..1), structural_features (booleans: has_numbered_sections, "
    "has_definitions_table, has_approval_matrix, has_thresholds, has_exceptions_section, "
    "has_audit_section, has_related_documents), extraction_strategy (list of strings), "
    "and warnings (list of {code, message}). Do not extract rules."
)

USER_TEMPLATE = 'POLICY DOCUMENT (markdown):\n"""\n{text}\n"""\n'

# Heuristic signal patterns.
_PATTERNS = {
    "has_numbered_sections": re.compile(r"^#{1,6}\s*\d+(\.\d+)*\s", re.M),
    "has_definitions_table": re.compile(r"\b(means|is defined as|definition)\b", re.I),
    "has_approval_matrix": re.compile(r"\b(approv|authority|sign[- ]?off)\w*", re.I),
    "has_thresholds": re.compile(r"(USD|EUR|\$|%|percent|exceed|threshold|limit|\d[\d,]*)", re.I),
    "has_exceptions_section": re.compile(r"\b(except|unless|waiver|override)\w*", re.I),
    "has_audit_section": re.compile(r"\b(audit|trace|log|retain|retention)\w*", re.I),
    "has_related_documents": re.compile(r"\b(related (policy|document)|see also|refer to)\b", re.I),
}


def _heuristic_profile(text: str, domain: Optional[str]) -> DocumentProfile:
    features = {k: bool(p.search(text)) for k, p in _PATTERNS.items()}
    strategy = ["section_clause_extraction"]
    if features["has_definitions_table"]:
        strategy.append("definition_extraction")
    if features["has_approval_matrix"]:
        strategy.append("approval_matrix_extraction")
    warnings = []
    if features["has_related_documents"]:
        warnings.append(ProfileWarning(
            code="RELATED_POLICIES_REFERENCED",
            message="Document references other policies that may be required.",
        ))
    return DocumentProfile(
        detected_source_type="business_policy",
        detected_domain=domain,
        domain_confidence=0.5 if domain else 0.0,
        structural_features=features,
        extraction_strategy=strategy,
        warnings=warnings,
    )


def profile_document(
    canonical_markdown: str,
    *,
    domain: Optional[str] = None,
    client=None,
) -> DocumentProfile:
    if client is not None:
        try:
            data = client.chat_json(
                SYSTEM_PROMPT, USER_TEMPLATE.format(text=canonical_markdown[:12000])
            )
            if data:
                if domain and not data.get("detected_domain"):
                    data["detected_domain"] = domain
                return DocumentProfile.model_validate(data)
        except Exception:
            pass
    return _heuristic_profile(canonical_markdown, domain)
