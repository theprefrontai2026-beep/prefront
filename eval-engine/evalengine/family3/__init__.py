"""Family 3 - Intent Conformance (call/scope/session/population checks over
a published intent_catalog.yaml). Not implemented yet - Phase B/C
(autonomous_build.md steps 12-14, 17). Consuming a catalog that doesn't exist
yet is a configuration gap, not an error (Hard Rule 9): callers should treat
an empty catalog as "not configured", never raise.
"""
