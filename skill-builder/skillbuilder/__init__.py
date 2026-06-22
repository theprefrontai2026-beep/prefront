"""Prefront Design-Time Skill Builder.

A policy compiler: business policy documents -> reviewed, versioned runtime
artifacts (canonical markdown, policy skill, extracted rules, test cases).

The LLM only ever produces *candidate* rules. Nothing here writes runtime
configuration: every rule lands as ``status: draft`` and must be promoted by a
human (draft -> reviewed -> approved) before runtime may enforce it.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
