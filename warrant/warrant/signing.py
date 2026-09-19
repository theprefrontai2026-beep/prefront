"""Ed25519 signing and offline verification.

Two promises in the spec drive every choice here. The Mission Authority must
"publish keys so any resource server can verify them offline", and the formats
must ship with "open-source verifiers per language". Both mean the same thing
in practice: verification must need nothing but a public key and these bytes —
no callback to us, no shared secret, no library only we have.

**Ed25519, and only Ed25519.** One algorithm, hardcoded, with no negotiation
and no `alg` field read from the input. Algorithm agility in a signed-token
format is where the alg-confusion family of bugs lives: a verifier that reads
the algorithm out of the object it is verifying can be told to use `none`, or
to verify an RSA signature with an HMAC key. The `algorithm` field on
`SignedMission` is descriptive — it is checked against the constant and
rejected if it differs, never used to select a code path.

**Detached signatures over canonical bytes**, not an enveloped token. A JWT
signs its own base64 encoding, which means a verifier must parse untrusted
input before it can check anything. Here the signature covers
`canonical_json(payload)` — so a verifier reconstructs the bytes from the
object it already holds and compares. There is no parse-before-verify step to
attack.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from .canonical import canonical_json

ALGORITHM = "Ed25519"
# JWK parameters for Ed25519, per RFC 8037. Constants rather than literals
# because they appear in both the export and the import path and a typo in one
# would produce a key set that looks right and verifies nothing.
JWK_KTY = "OKP"
JWK_CRV = "Ed25519"


class SigningError(Exception):
    """A key could not be loaded, or a signature could not be produced."""


class VerificationError(Exception):
    """A signature did not verify.

    Deliberately NOT a subclass of `SigningError`: callers routinely catch
    verification failure as an expected control-flow outcome (an attacker
    presenting a forged Mission is a Tuesday), while a signing failure is
    always a misconfiguration. Sharing a base class invites one `except` to
    swallow both, turning "our key is missing" into "that Mission was forged".
    """


def b64u_encode(raw: bytes) -> str:
    """base64url without padding — the encoding every JOSE-adjacent format uses."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    """Decode base64url, restoring the padding the encoder stripped.

    Rejects anything that is not valid base64url rather than letting Python's
    lenient decoder discard stray characters: a signature that decodes only
    after silently dropping a byte is a forgery that verifies.
    """
    if not isinstance(text, str):
        raise VerificationError(f"expected a base64url string, got {type(text).__name__}")
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception as exc:  # binascii.Error and friends
        raise VerificationError(f"value is not valid base64url: {exc}") from exc


@dataclass(frozen=True)
class VerifyKey:
    """A public key plus the id a signed object names it by.

    This is everything a third party needs, which is the point: an air-gapped
    resource server holds a list of these and can verify every Mission we ever
    issue without reaching us.
    """

    key_id: str
    public_key: ed25519.Ed25519PublicKey

    def verify(self, payload: Any, signature: str) -> None:
        """Raise `VerificationError` unless `signature` covers `payload`.

        Raises rather than returning a bool. A bool invites `if verify(...)`
        to be written as `verify(...)` by someone in a hurry, and that typo is
        a silent accept-everything — the single worst bug this file could have.
        """
        raw = b64u_decode(signature)
        try:
            self.public_key.verify(raw, canonical_json(payload))
        except InvalidSignature as exc:
            raise VerificationError(
                f"signature does not verify under key {self.key_id!r}"
            ) from exc

    def to_jwk(self) -> dict[str, str]:
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "kty": JWK_KTY,
            "crv": JWK_CRV,
            "kid": self.key_id,
            "x": b64u_encode(raw),
            "alg": "EdDSA",
            "use": "sig",
        }

    @classmethod
    def from_jwk(cls, jwk: dict[str, Any]) -> "VerifyKey":
        """Load a public key from a JWK, refusing anything that is not ours.

        Every field is checked before the key is built. A verifier that accepts
        a JWK with the wrong `kty` because it only looked at `x` is a verifier
        that can be handed an attacker-chosen key type.
        """
        if jwk.get("kty") != JWK_KTY or jwk.get("crv") != JWK_CRV:
            raise VerificationError(
                f"unsupported key: expected kty={JWK_KTY} crv={JWK_CRV}, got "
                f"kty={jwk.get('kty')!r} crv={jwk.get('crv')!r}. This package "
                "signs and verifies Ed25519 only, by design"
            )
        kid = jwk.get("kid") or ""
        if not kid:
            raise VerificationError("JWK has no kid: a key that cannot be named cannot be selected")
        raw = b64u_decode(jwk.get("x") or "")
        try:
            pub = ed25519.Ed25519PublicKey.from_public_bytes(raw)
        except Exception as exc:
            raise VerificationError(f"JWK {kid!r} does not hold a valid Ed25519 key: {exc}") from exc
        return cls(key_id=kid, public_key=pub)


@dataclass(frozen=True)
class SigningKey:
    """A private key held by exactly one component.

    Two components sign in this system and they must never share a key: the
    Mission Authority proves a HUMAN approved a task, the Intent Binder proves
    only which agent process made a claim. One key for both would let a
    compromised agent mint its own consent.
    """

    key_id: str
    private_key: ed25519.Ed25519PrivateKey

    @classmethod
    def generate(cls, key_id: str) -> "SigningKey":
        if not key_id:
            raise SigningError("a signing key must have a key_id so signed objects can name it")
        return cls(key_id=key_id, private_key=ed25519.Ed25519PrivateKey.generate())

    @classmethod
    def from_raw(cls, key_id: str, raw: bytes) -> "SigningKey":
        """Load from the 32 raw private bytes (what a KMS or a mounted secret holds)."""
        if not key_id:
            raise SigningError("a signing key must have a key_id")
        try:
            return cls(key_id=key_id, private_key=ed25519.Ed25519PrivateKey.from_private_bytes(raw))
        except Exception as exc:
            raise SigningError(f"not a valid Ed25519 private key: {exc}") from exc

    def sign(self, payload: Any) -> str:
        """Sign the canonical bytes of `payload`, returning base64url."""
        return b64u_encode(self.private_key.sign(canonical_json(payload)))

    def verify_key(self) -> VerifyKey:
        return VerifyKey(key_id=self.key_id, public_key=self.private_key.public_key())

    def private_bytes(self) -> bytes:
        """Raw private bytes, for persisting into a vault or KMS.

        Named explicitly rather than exposed as a property so it never appears
        by accident in a repr, a log line or a serialized config.
        """
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )


class KeySet:
    """The published public keys, as a resource server holds them.

    Mutable and lookup-by-kid, because key ROTATION is the normal case: during
    a rotation two keys are live at once, objects signed under the old one must
    keep verifying until they expire, and a verifier that can hold only one key
    forces a flag day on every customer.

    Selection is by `kid` and nothing else. In particular this class will never
    "try every key until one works" — that turns an unknown-key error, which is
    a real signal that something is wrong, into a successful verification under
    a key nobody expected.
    """

    def __init__(self, keys: Optional[list[VerifyKey]] = None) -> None:
        self._keys: dict[str, VerifyKey] = {}
        for key in keys or []:
            self.add(key)

    def add(self, key: VerifyKey) -> None:
        existing = self._keys.get(key.key_id)
        if existing is not None and existing.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ) != key.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ):
            # Silently replacing would mean a published kid quietly changed
            # meaning, and every object signed under the old key would start
            # reading as a forgery with no explanation.
            raise SigningError(
                f"key id {key.key_id!r} is already published with different key "
                "material: a kid must name one key forever. Rotate by adding a "
                "new kid, never by rebinding an old one"
            )
        self._keys[key.key_id] = key

    def get(self, key_id: str) -> VerifyKey:
        key = self._keys.get(key_id)
        if key is None:
            raise VerificationError(
                f"no published key with id {key_id!r}: refusing to guess. An "
                "object naming an unknown key is unverifiable, not unsigned"
            )
        return key

    def verify(self, payload: Any, key_id: str, signature: str, algorithm: str = ALGORITHM) -> None:
        """Verify `signature` over `payload` under the named key.

        `algorithm` is CHECKED, never dispatched on — see the module docstring.
        """
        if algorithm != ALGORITHM:
            raise VerificationError(
                f"unsupported algorithm {algorithm!r}: this profile is {ALGORITHM} "
                "only, and algorithm negotiation is where signature-confusion "
                "bugs come from"
            )
        self.get(key_id).verify(payload, signature)

    def to_jwks(self) -> dict[str, list[dict[str, str]]]:
        """The JWKS document the Authority publishes.

        Sorted by kid so the document is byte-stable across restarts — it gets
        cached, diffed and checked into customers' configuration, and a set
        that reorders itself on every boot looks like a key change.
        """
        return {"keys": [k.to_jwk() for k in sorted(self._keys.values(), key=lambda k: k.key_id)]}

    @classmethod
    def from_jwks(cls, doc: dict[str, Any]) -> "KeySet":
        keys = doc.get("keys")
        if not isinstance(keys, list):
            raise VerificationError("JWKS document has no 'keys' array")
        return cls([VerifyKey.from_jwk(k) for k in keys])

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key_id: object) -> bool:
        return key_id in self._keys
