"""Prefront Semantic MCP Server (POC).

A thin runtime that loads the design-time ``query_templates.yaml`` produced by
``semantic-layer/`` and exposes each template as an MCP tool. Calling a tool runs
the template's parameterized SQL against the configured Postgres and returns the
rows. The agent only ever sees the available queries as typed tools — the server
is a function wrapper around each approved query.

POC scope: no policy, caller context, sensitivity, approval, or writes. It reads
``query_templates.yaml`` as data and never imports the semantic-layer package.
"""

from __future__ import annotations

__version__ = "0.1.0"
