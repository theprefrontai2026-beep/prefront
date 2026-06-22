"""Prefront Semantic Layer Builder.

A design-time agentic program that turns two reviewed inputs —

  * a governed datasource schema (PostgreSQL DDL), and
  * approved business policy rules (skill-builder output)

— into a versioned *semantic contract*: business entities mapped onto real
tables/columns, approved join paths, field sensitivity, intent bindings, and
MCP tool interfaces the customer's LLM can call. The LLM is used ONLY at design
time to *suggest* the semantic mapping; everything it produces is candidate
output that must pass schema + publish validation before it is emitted.

See ``prefront_semantic_layer_design.md`` for the full design. Hard rules
(design §23) are enforced by ``schema.py`` and ``validate.py``.
"""

from __future__ import annotations

__version__ = "0.1.0"
