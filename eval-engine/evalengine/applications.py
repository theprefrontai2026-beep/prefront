"""Per-application configuration: which artifacts each subject application is
evaluated against, and which checks it runs.

Until this existed eval-engine was single-tenant by construction — one
``EVAL_RULE_PACK_PATH``, one ``EVAL_INTENT_CATALOG_PATH``, one
``EVAL_COMPLIANCE_OVERLAY_PATH``, one ``EVAL_TRACE_BINDING_PATH`` for the whole
deployment, loaded once at module scope in ``api.py`` and handed to the worker
at construction. Every session from every application was therefore evaluated
against the same rule pack, and an application with no artifacts of its own
still reported another application's rule count as if it were its own
(``application_isolation_design.md`` §2, Phase 3).

WHAT THIS DOES NOT CHANGE
-------------------------
Nothing, unless a deployment configures it. With ``EVAL_APPLICATIONS_PATH``
unset the registry reports ``configured=False`` and ``for_app()`` returns the
deployment-wide defaults for every application — byte-identical behaviour to
before (Hard Rule 9). A single-application deployment never needs this file.

THE VERSION KEY FALLS OUT
-------------------------
``evaluate.version_key`` already composes the artifacts' OWN versions
(``rule_pack.source_skill@version``, ``catalog@version``, ``binding.version``,
``visibility.version``) plus ``checks@version``. So resolving a different rule
pack for a different application produces a different version key for free —
each application's sessions are evaluated, and re-evaluated, independently of
every other's. No separate "app config version" is needed, and adding one would
be a second thing to keep in step with the first.

DOMAIN INDEPENDENCE
-------------------
Every application id, artifact path and check id here comes from a YAML file a
deployment supplies. This module names none of them (Hard Rule 1), which is
what ``tests/test_domain_independence.py`` enforces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import yaml

from .binding import BindingProfile
from .checks import CheckSettings
from .compliance import Overlay
from .family1.compilepack import RulePack
from .family3.catalog import IntentCatalog
from .visibility import VisibilityProfile


@dataclass(frozen=True)
class AppConfig:
    """Everything an evaluation pass needs, resolved for one application."""

    app_id: str
    binding: BindingProfile
    visibility: VisibilityProfile
    rule_pack: RulePack
    catalog: IntentCatalog
    overlay: Optional[Overlay] = None
    # None = "this application states nothing", so the deployment-wide stored
    # set applies. An EMPTY frozenset is a different statement — "this
    # application runs every check" — and overrides the deployment default.
    # Collapsing the two would make "inherit" unexpressible.
    disabled_checks: Optional[frozenset[str]] = None

    def settings(self, deployment: CheckSettings) -> CheckSettings:
        """This application's check settings.

        Precedence: a per-application set REPLACES the deployment-wide one when
        the application declares one, and INHERITS it when it does not. The
        rejected alternative was intersecting the two, which gives two switches
        whose combined effect cannot be predicted from either — the outcome
        `TODO` entry 8 itself calls the worst of the three.
        """
        if self.disabled_checks is None:
            return deployment
        return CheckSettings(disabled=self.disabled_checks)


class Registry:
    """Applications and their configuration, loaded from one YAML artifact.

    Artifacts are loaded ONCE per distinct path and shared between applications
    that name the same file, so two applications pointing at one rule pack cost
    one parse rather than two.
    """

    def __init__(self, defaults: AppConfig, path: str = "") -> None:
        self._defaults = defaults
        self._path = path
        self._apps: dict[str, AppConfig] = {}
        self.version: str = ""
        if path:
            self._load(path)

    @property
    def configured(self) -> bool:
        return bool(self._apps)

    @property
    def app_ids(self) -> list[str]:
        return sorted(self._apps)

    def for_app(self, app_id: str) -> AppConfig:
        """This application's configuration, or the deployment-wide defaults.

        An UNKNOWN application id falls back to the defaults rather than
        raising: a session can be ingested for an application nobody has
        registered yet, and refusing to evaluate it would lose the evidence.
        It is reported as unregistered instead (see api.py's /eval/applications).
        """
        return self._apps.get(app_id, self._defaults)

    def registered(self, app_id: str) -> bool:
        return app_id in self._apps

    # --- loading -------------------------------------------------------------

    def _load(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        body = raw.get("applications") or {}
        self.version = str(body.get("version", "1"))
        cache: dict[tuple[str, str], Any] = {}

        def load_one(kind: str, p: str, loader, fallback):
            if not p:
                return fallback
            key = (kind, p)
            if key not in cache:
                # A path that is SET but unreadable is a misconfiguration, not
                # "unconfigured" — same rule as api.py's _load_artifact. Let it
                # raise: a registry naming a missing rule pack must fail at
                # load, not silently evaluate that application against nothing.
                cache[key] = loader(p)
            return cache[key]

        from .binding import load as load_binding
        from .compliance import load_overlay
        from .family1.compilepack import load as load_rule_pack
        from .family3.catalog import load as load_catalog
        from .visibility import load as load_visibility

        for entry in body.get("apps") or []:
            app_id = str(entry.get("id") or "").strip()
            if not app_id:
                continue
            arts = entry.get("artifacts") or {}
            checks = entry.get("checks") or {}
            disabled = checks.get("disabled")
            self._apps[app_id] = AppConfig(
                app_id=app_id,
                binding=load_one("binding", str(arts.get("trace_binding") or ""),
                                 load_binding, self._defaults.binding),
                visibility=load_one("visibility", str(arts.get("visibility_profile") or ""),
                                    load_visibility, self._defaults.visibility),
                rule_pack=load_one("rule_pack", str(arts.get("rule_pack") or ""),
                                   load_rule_pack, self._defaults.rule_pack),
                catalog=load_one("catalog", str(arts.get("intent_catalog") or ""),
                                 load_catalog, self._defaults.catalog),
                overlay=load_one("overlay", str(arts.get("compliance_overlay") or ""),
                                 load_overlay, self._defaults.overlay),
                disabled_checks=(frozenset(str(c) for c in disabled)
                                 if disabled is not None else None),
            )
