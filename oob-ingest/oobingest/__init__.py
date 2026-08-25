"""Prefront OOB ingestion: out-of-band observability for the governed runtime.

Nothing in here sits on the request path of a governed call. Spans reach this
service either by being pulled from Arize Phoenix (its REST API) or by an
OTLP/HTTP fan-out from the tracing module; both land in ClickHouse, and the
query API in ``api.py`` reads them back for the UI.
"""
