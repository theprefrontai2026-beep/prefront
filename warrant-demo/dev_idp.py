"""A stand-in identity provider, for the demo only.

=============================================================================
THIS IS A TEST DOUBLE. It mints a token for ANY subject ANYONE asks for, with
no password, no MFA and no authorization of any kind. It exists so the demo can
show the real identity chain end to end without requiring an Okta tenant. It
must never run anywhere that matters, and nothing in `warrant-service` treats
it as special — the PDS verifies its tokens exactly as it would verify Okta's,
against a published JWKS, with issuer and audience checked.
=============================================================================

It is a separate container rather than a thread inside the console, because
that is what it is: a separate system the enforcement plane trusts by
configuration. Running it separately also removes an ordering problem — the PDS
fetches the JWKS lazily, and the console needs a token before it can do
anything, so an IdP embedded in the console would have to be listening before
the console had started listening.

Signing is hand-written on top of the engine's Ed25519 primitives rather than
pulling in a JWT library, because the demo image ships the standard library
plus `cryptography` and that is worth keeping. Note the asymmetry: hand-rolling
token CREATION is safe — there is no untrusted input, and a mistake produces a
token that fails to verify. Hand-rolling token VERIFICATION is where the
alg-confusion family of bugs lives, and the service uses PyJWT for exactly that
reason.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "warrant"))

from warrant import SigningKey  # noqa: E402
from warrant.signing import b64u_encode  # noqa: E402

AUDIENCE = os.environ.get("DEV_IDP_AUDIENCE", "warrant-pds")
ISSUER = os.environ.get("DEV_IDP_ISSUER", "http://dev-idp:8160")
TTL_SECONDS = int(os.environ.get("DEV_IDP_TTL", "3600"))

KEY = SigningKey.generate("dev-idp-1")


def mint(subject: str) -> str:
    """One EdDSA-signed JWT for `subject`.

    `kid` in the header is what lets the verifier find the key in the JWKS
    below — the same mechanism a real IdP uses, so rotation behaves the same
    way here as it would in production.
    """
    now = int(time.time())
    header = {"alg": "EdDSA", "typ": "JWT", "kid": KEY.key_id}
    claims = {
        "sub": subject,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + TTL_SECONDS,
    }
    segments = [
        b64u_encode(json.dumps(h, separators=(",", ":"), sort_keys=True).encode())
        for h in (header, claims)
    ]
    signing_input = ".".join(segments).encode()
    signature = b64u_encode(KEY.private_key.sign(signing_input))
    return ".".join(segments + [signature])


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if route in ("/.well-known/jwks.json", "/jwks.json"):
            self._send(json.dumps({"keys": [KEY.verify_key().to_jwk()]}).encode())
        elif route == "/healthz":
            self._send(b'{"ok":true,"warning":"test double: mints tokens for anyone"}')
        else:
            self._send(b'{"error":"not_found"}', status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/token":
            self._send(b'{"error":"not_found"}', status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            subject = body.get("subject") or ""
        except Exception:
            self._send(b'{"error":"invalid_json"}', status=400)
            return
        if not subject:
            self._send(b'{"error":"subject is required"}', status=400)
            return
        self._send(json.dumps({
            "subject_token": mint(subject),
            "expires_in": TTL_SECONDS,
            "warning": "issued by a demo test double; anyone may request any subject",
        }).encode())

    def log_message(self, *args) -> None:
        """Quiet: a terminal scrolling request logs behind a live demo reads as
        something going wrong."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=int(os.environ.get("DEV_IDP_PORT", "8160")))
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args(argv)

    print(f"dev-idp (TEST DOUBLE)  iss={ISSUER}  aud={AUDIENCE}  kid={KEY.key_id}")
    print("  mints a token for any subject, with no authentication. Demo use only.")
    print(f"  listening on http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
