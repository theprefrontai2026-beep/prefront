"""`python -m warrantservice` — run the PDS.

Announces its posture at startup rather than waiting for someone to wonder.
Two states are worth saying out loud, because both are legitimate and both
silently change what the service does:

  an EMPTY action registry denies every side-effect call (fail closed), and
  a GENERATED authority key means Missions stop verifying at the next restart.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from .config import ConfigError, from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m warrantservice")
    parser.add_argument("--host", default=os.environ.get("WARRANT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WARRANT_PORT", "8150")))
    args = parser.parse_args(argv)

    try:
        settings = from_env()
    except ConfigError as exc:
        print(f"warrant-service: {exc}")
        return 1

    print(f"warrant-service  issuer={settings.issuer}  policy={settings.policy_version}")
    if settings.registry_path:
        print(f"  {len(settings.registry)} action classes from {settings.registry_path}")
    else:
        print("  NO action registry configured (WARRANT_ACTION_REGISTRY_PATH unset): "
              "every call naming an unregistered action fails closed and is denied")
    if not settings.authority_key_supplied:
        print("  authority key GENERATED for this process: Missions signed now stop "
              "verifying after a restart. Set WARRANT_AUTHORITY_KEY for anything real")
    print(f"  listening on http://{args.host}:{args.port}  (docs at /docs)")

    from .app import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
