"""Shipped framework packs (Layer A of compliance_design.md). Data, not code:
each YAML names control classes and abstract data classes only - never a
column, table, role, channel or a deployment's policy section. Loaded by
`evalengine.compliance.packs.load_packs`; a deployment can add or replace
one via EVAL_FRAMEWORK_PACKS_DIR without touching the image.

Framework citations (article / criterion / requirement numbers) were written
from working knowledge and not verified against the primary texts - see the
header of compliance_design.md.
"""
