"""Warrant's enforcement plane as a service.

`warrant/` is the engine: a library with one dependency, embeddable in a
process that has never heard of HTTP. This package is the socket it decides
through — the wire codec, the configuration that lets a deployment name its own
action classes, the FastAPI app, and a client.

The split is load-bearing rather than tidy. The spec promises resource servers
can verify a Mission offline with published keys and an open verifier; a
verifier that had to install a web framework would satisfy neither the letter
nor the spirit of that.

**Every submodule here is imported lazily, and that is a requirement rather
than an optimisation.** The client goes into a customer's AGENT process, where
the whole promise is "under 50 lines to integrate" — not "plus a resolution
argument with your existing HTTP library". So `RemotePolicyDecisionService`
must cost nothing but the standard library to import, which means this file
cannot eagerly pull in `config` (PyYAML) or `app` (FastAPI). An earlier version
imported `config` at module scope and broke exactly this: the demo container,
which ships no PyYAML, died on startup importing a client that needs none.
`tests/test_client_is_stdlib_only.py` now holds the line.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "Settings",
    "from_env",
    "create_app",
    "RemotePolicyDecisionService",
    "ServiceError",
]

# name -> the submodule that defines it. Kept as data so adding a symbol is one
# line and cannot accidentally reintroduce an eager import.
_LAZY = {
    "ConfigError": "config",
    "Settings": "config",
    "from_env": "config",
    "RemotePolicyDecisionService": "client",
    "ServiceError": "client",
}


def create_app(settings=None):
    """The FastAPI app. Imported on call so the client never pays for it."""
    from .app import create_app as _create

    return _create(settings)


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(f".{module}", __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)
