"""`python -m warrantservice.credentials` — mint a service credential.

The secret is printed ONCE and never stored: the file keeps only its SHA-256,
so losing the secret means minting a new one rather than recovering the old.
That is the property worth having — a credentials file that leaks is then a
list of hashes, not a list of working keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .auth import SCOPES, hash_secret, mint_secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m warrantservice.credentials")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="mint a credential and print it once")
    new.add_argument("client_id")
    new.add_argument("--scopes", required=True,
                     help="comma-separated; one of: " + ", ".join(sorted(SCOPES)))
    new.add_argument("--description", default="")
    new.add_argument("--append-to", default="",
                     help="credentials file to add this client to (created if absent)")

    sub.add_parser("scopes", help="list the scopes and what each one opens")

    args = parser.parse_args(argv)

    if args.command == "scopes":
        width = max(len(s) for s in SCOPES)
        for name, what in sorted(SCOPES.items()):
            print(f"  {name:<{width}}  {what}")
        return 0

    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        print(f"unknown scope(s) {unknown}; known: {', '.join(sorted(SCOPES))}")
        return 1

    secret = mint_secret()
    entry = {
        "client_id": args.client_id,
        "secret_sha256": hash_secret(secret),
        "scopes": scopes,
    }
    if args.description:
        entry["description"] = args.description

    if args.append_to:
        path = Path(args.append_to)
        doc = {"clients": []}
        if path.exists():
            loaded = (yaml.safe_load(path.read_text())
                      if path.suffix in (".yaml", ".yml") else json.loads(path.read_text()))
            doc = loaded or doc
            if any(c.get("client_id") == args.client_id for c in doc.get("clients", [])):
                print(f"{args.client_id!r} is already in {path}; "
                      "remove it first rather than having two entries with one id")
                return 1
        doc.setdefault("clients", []).append(entry)
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False)
            if path.suffix in (".yaml", ".yml") else json.dumps(doc, indent=2)
        )
        print(f"added {args.client_id!r} to {path}")
    else:
        print(yaml.safe_dump({"clients": [entry]}, sort_keys=False))

    # Last, and on its own, so it is the thing left on screen.
    print(f"\ncredential (shown once, not stored):\n\n    {args.client_id}.{secret}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
