"""Proof-of-possession for task-tree tokens (DPoP, RFC 9449).

The spec's promise is that "a stolen token is useless". A bearer token cannot
make that promise: whoever holds the bytes may use them. DPoP fixes that by
binding a token to a key — the token carries the SHA-256 thumbprint of the
holder's public key (`cnf.jkt`), and every presentation of it must come with a
fresh proof signed by the matching private key. Steal the token and you have a
string you cannot use; you need the key, which never leaves the agent process.

DPoP rather than mTLS-bound tokens (RFC 8705) for two reasons. It needs no TLS
infrastructure at the customer, which matters for a product whose integration
budget is "one decorator"; and the agent process already holds an Ed25519 key
for signing Action Attestations, so the same key can prove possession. That
reuse buys a property neither mechanism has alone: the PDS can check that the
key which proved possession of the token is the key that signed the
attestation, so a token and a claim cannot come from different places.

What a proof must survive, and where each is checked below:

  forged signature    verified against the key embedded in the proof header,
                      whose thumbprint must equal the token's `cnf.jkt` — so
                      an attacker's own key produces a proof that verifies
                      against itself and then fails the binding
  replay              `jti` is remembered for the acceptance window
  moved to another    `htm`/`htu` pin the method and URL the proof was made for
    endpoint
  token substitution  `ath` binds the proof to one specific access token
  stale proof         `iat` must be inside a narrow window
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from warrant.signing import b64u_encode

# NOTE: PyJWT is imported lazily, inside `ProofVerifier.verify`. Creating a
# proof and computing a thumbprint need only the standard library and the
# engine's Ed25519 key, and the CLIENT does exactly those two things — so an
# agent process gets proof-of-possession without inheriting a JWT dependency.
# Only the service side, which must verify untrusted input, needs the library.
# An eager import here broke the demo container; the guard is
# tests/test_client_is_stdlib_only.py.

# Asymmetric only, for the same reason as subject tokens: with HMAC the key
# that verifies is the key that signs, so a verifier could mint proofs.
ALLOWED_ALGORITHMS = ("EdDSA", "ES256", "ES384", "RS256")
DPOP_TYP = "dpop+jwt"

# RFC 7638 §3.2: the members a thumbprint is computed over, per key type, in
# lexicographic order. Anything else in the JWK is excluded — which is what
# makes the thumbprint stable when a key is republished with extra metadata.
_THUMBPRINT_MEMBERS = {
    "OKP": ("crv", "kty", "x"),
    "EC": ("crv", "kty", "x", "y"),
    "RSA": ("e", "kty", "n"),
    "oct": ("k", "kty"),
}


class DpopError(Exception):
    """A proof was missing, malformed, or did not bind to what it claimed."""


def thumbprint(jwk: dict[str, Any]) -> str:
    """The RFC 7638 JWK thumbprint, base64url — a token's `cnf.jkt`.

    Computed over the required members only, in lexicographic order, with no
    whitespace. That canonicalization is the whole point: the same key must
    produce the same thumbprint whoever serializes it, or binding a token to a
    key would depend on how the key was written down.
    """
    kty = jwk.get("kty")
    members = _THUMBPRINT_MEMBERS.get(kty)
    if not members:
        raise DpopError(f"cannot compute a thumbprint for key type {kty!r}")
    missing = [m for m in members if not jwk.get(m)]
    if missing:
        raise DpopError(f"JWK is missing {missing} required for a {kty} thumbprint")
    canonical = json.dumps(
        {m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True
    ).encode()
    return b64u_encode(hashlib.sha256(canonical).digest())


def access_token_hash(token: str) -> str:
    """`ath`: base64url SHA-256 of the access token it is presented with."""
    return b64u_encode(hashlib.sha256(token.encode()).digest())


class ReplayCache:
    """Remembers `jti`s for as long as a proof could still be accepted.

    Bounded by TIME rather than by count, because the guarantee has to hold for
    the whole acceptance window: an LRU that evicted early would let a proof be
    replayed exactly when the system was busiest. Entries older than the window
    are dropped on write, so the cache is self-limiting without a sweeper.

    In-memory, so two replicas do not share it — a proof accepted by one could
    be replayed against another. That is a real limit of this stage and is
    documented rather than papered over; the fix is a shared store, not a
    bigger cache.
    """

    def __init__(self, window_seconds: int) -> None:
        self.window = window_seconds
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_record(self, jti: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            cutoff = now - self.window
            if self._seen:
                # Prune on write; cheap while the window is short, and it keeps
                # the structure bounded without a background task.
                self._seen = {k: t for k, t in self._seen.items() if t > cutoff}
            if jti in self._seen:
                raise DpopError(
                    f"DPoP proof {jti!r} has already been used. A proof is "
                    "single-use within its acceptance window"
                )
            self._seen[jti] = now

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


@dataclass(frozen=True)
class Proof:
    """A verified proof, and what it binds to."""

    jkt: str
    jwk: dict[str, Any]
    jti: str
    htm: str
    htu: str
    ath: str


class ProofVerifier:
    def __init__(self, acceptance_window_seconds: int = 60) -> None:
        self.window = acceptance_window_seconds
        self.replays = ReplayCache(acceptance_window_seconds * 2)

    def verify(
        self,
        proof_jwt: str,
        *,
        method: str,
        url: str,
        access_token: str = "",
        now: Optional[float] = None,
    ) -> Proof:
        """Verify a proof for one request, or raise."""
        if not proof_jwt or not isinstance(proof_jwt, str):
            raise DpopError("a DPoP proof is required and must be a string")

        import jwt  # lazy: see the module note above

        try:
            header = jwt.get_unverified_header(proof_jwt)
        except Exception as exc:
            raise DpopError(f"DPoP proof header is unreadable: {exc}") from exc

        if header.get("typ") != DPOP_TYP:
            raise DpopError(
                f"DPoP proof must carry typ={DPOP_TYP!r}, got {header.get('typ')!r}. "
                "The type header is what stops a token being replayed as a proof"
            )
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise DpopError(
                f"DPoP proof algorithm {alg!r} is not accepted; this profile allows "
                f"{', '.join(ALLOWED_ALGORITHMS)}"
            )

        embedded = header.get("jwk")
        if not isinstance(embedded, dict):
            raise DpopError("DPoP proof header must embed the public key as 'jwk'")
        # A private key in the header is either a catastrophic client bug or an
        # attempt to confuse the verifier. Either way it is not a proof.
        for private_member in ("d", "p", "q", "dp", "dq", "qi", "k"):
            if private_member in embedded:
                raise DpopError(
                    f"DPoP proof embeds private key material ({private_member!r}); "
                    "refusing it outright"
                )

        jkt = thumbprint(embedded)

        try:
            key = jwt.PyJWK.from_dict({**embedded, "alg": alg}).key
        except Exception as exc:
            raise DpopError(f"DPoP proof embeds an unusable key: {exc}") from exc

        try:
            claims = jwt.decode(
                proof_jwt,
                key,
                algorithms=[alg],
                # A proof carries no aud/iss/exp; its freshness is `iat` plus a
                # narrow window, checked below. Disabling those here is correct
                # rather than lax — requiring them would reject conformant
                # proofs from every other implementation.
                options={"verify_aud": False, "verify_exp": False,
                         "verify_iss": False, "require": ["jti", "htm", "htu", "iat"]},
            )
        except jwt.InvalidTokenError as exc:
            raise DpopError(f"DPoP proof did not verify: {exc}") from exc

        if claims.get("htm", "").upper() != method.upper():
            raise DpopError(
                f"DPoP proof was made for {claims.get('htm')!r}, not {method!r}"
            )
        if claims.get("htu") != url:
            raise DpopError(
                f"DPoP proof was made for {claims.get('htu')!r}, not {url!r}. A proof "
                "is valid for one endpoint, so one captured at a harmless endpoint "
                "cannot be presented at a dangerous one"
            )

        now = time.time() if now is None else now
        iat = claims.get("iat")
        if not isinstance(iat, (int, float)) or abs(now - iat) > self.window:
            raise DpopError(
                f"DPoP proof timestamp is outside the {self.window}s acceptance window"
            )

        if access_token:
            expected = access_token_hash(access_token)
            if claims.get("ath") != expected:
                raise DpopError(
                    "DPoP proof is not bound to the access token presented with it "
                    "(ath mismatch), so the two did not arrive together"
                )

        self.replays.check_and_record(str(claims["jti"]), now=now)

        return Proof(
            jkt=jkt,
            jwk=embedded,
            jti=str(claims["jti"]),
            htm=str(claims.get("htm", "")),
            htu=str(claims.get("htu", "")),
            ath=str(claims.get("ath", "")),
        )


def make_proof(
    key,
    *,
    method: str,
    url: str,
    access_token: str = "",
    now: Optional[int] = None,
) -> str:
    """Create a DPoP proof with an engine `SigningKey` (Ed25519).

    The CLIENT half, kept beside the verifier so the two cannot drift — a proof
    format that only one side understands is a bug that shows up as an
    authentication failure nobody can explain.

    Hand-written rather than via a JWT library, for the same reason the demo's
    stand-in issuer is: creating a token involves no untrusted input, and a
    mistake yields a proof that fails to verify. It is VERIFICATION where being
    clever is dangerous, and that side uses PyJWT.
    """
    import json as _json
    import uuid as _uuid

    header = {
        "typ": DPOP_TYP,
        "alg": "EdDSA",
        "jwk": key.verify_key().to_jwk(),
    }
    # `use`/`kid` are fine to publish but are not thumbprint members, so their
    # presence cannot change the binding; `alg` inside the embedded jwk would
    # be redundant with the header's and is left out.
    header["jwk"] = {k: v for k, v in header["jwk"].items() if k in ("kty", "crv", "x")}

    claims: dict[str, Any] = {
        "jti": _uuid.uuid4().hex,
        "htm": method.upper(),
        "htu": url,
        "iat": int(time.time()) if now is None else now,
    }
    if access_token:
        claims["ath"] = access_token_hash(access_token)

    segments = [
        b64u_encode(_json.dumps(part, separators=(",", ":"), sort_keys=True).encode())
        for part in (header, claims)
    ]
    signing_input = ".".join(segments).encode()
    return ".".join(segments + [b64u_encode(key.private_key.sign(signing_input))])


def key_thumbprint(key) -> str:
    """The `jkt` a token should be bound to, for an engine `SigningKey`."""
    jwk = key.verify_key().to_jwk()
    return thumbprint({k: v for k, v in jwk.items() if k in ("kty", "crv", "x")})
