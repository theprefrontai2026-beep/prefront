"""The end user's identity, taken from the customer's IdP and nowhere else.

The spec is explicit that Warrant is not a second identity store: `sub` only
ever comes from Okta, Entra or Google. Before this file existed, the service
took the subject as a STRING in the request body and compared it to the
Mission's — so `pds._check_subject` read like an identity control and was not
one. Anyone who could call the service could claim to be anyone.

What changes: when an issuer is configured, the caller presents the IdP's own
token and this module verifies it — signature against the issuer's published
keys, plus issuer, audience and expiry — and the subject is whatever the
verified `sub` claim says. The body's `token_subject` is then REFUSED rather
than ignored, because leaving the unverified path open next to the verified one
means the weakest link is still there for anyone who finds it.

Verification uses PyJWT rather than anything hand-rolled. JWT validation is the
canonical place to not be clever: algorithm confusion, unverified `alg`, `none`,
and key-type substitution are all live foot-guns, and a library that has been
audited for them is worth more than code that looks right here.

Two choices worth naming:

**Algorithms are configured, never read from the token.** The `algorithms=`
argument is an allow-list, and the default is asymmetric only. A deployment
that added an HMAC algorithm here would let anyone holding the (public)
verification key mint tokens, which is exactly the confusion the allow-list
exists to prevent.

**Audience is required when configured, and configuring it is strongly
advised.** A token minted for a different application at the same issuer is a
valid token; without an audience check it is also a valid subject here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import jwt
from jwt import PyJWKClient

# Asymmetric only. Nothing symmetric belongs here: with HMAC, the key used to
# verify is the key used to sign, so a verifier holding it can forge.
DEFAULT_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA")
_SYMMETRIC_PREFIXES = ("HS", "none")


class OidcConfigError(RuntimeError):
    """The identity configuration cannot be used."""


class SubjectError(Exception):
    """A subject token was missing, malformed or did not verify."""


@dataclass(frozen=True)
class OidcSettings:
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    leeway_seconds: int = 30
    # How long the key cache is reused before a scheduled refresh.
    cache_seconds: int = 600
    # How soon an UNKNOWN kid may force an out-of-band refresh. This exists to
    # stop a flood of tokens naming random kids from turning into a flood of
    # requests to the IdP, so it should not be zero outside a test. Stated
    # explicitly rather than inherited from PyJWT's default (also 30s) because
    # it is a security/availability trade a deployment may want to tune, and an
    # inherited default is one nobody knows is there.
    refresh_cooldown_seconds: int = 30

    @property
    def enabled(self) -> bool:
        return bool(self.issuer)


class SubjectVerifier:
    """Turns an IdP token into a subject, or refuses.

    Holds a `PyJWKClient`, which caches the issuer's keys so a steady state
    costs no network call per decision, and re-fetches when a token names a
    `kid` it has not seen — so a key rotation at the IdP heals without a
    restart.

    That re-fetch is rate limited (`refresh_cooldown_seconds`), and the limit
    is real rather than notional: within the cooldown, a token naming a
    genuinely new key is refused. In practice an IdP publishes a new key before
    it signs with it, so the scheduled refresh has already seen it; the
    cooldown only bites when a rotation is faster than the window. It cannot be
    removed, because without it a flood of tokens naming random kids becomes a
    flood of requests to the IdP.
    """

    def __init__(self, settings: OidcSettings) -> None:
        self.settings = settings
        self._jwks: Optional[PyJWKClient] = None
        if not settings.enabled:
            return

        bad = [a for a in settings.algorithms
               if any(a.startswith(p) for p in _SYMMETRIC_PREFIXES)]
        if bad:
            raise OidcConfigError(
                f"refusing symmetric or 'none' algorithm(s) {bad} for subject "
                "tokens: with HMAC the verification key is the signing key, so "
                "anything able to verify a token could also mint one"
            )
        url = settings.jwks_url or f"{settings.issuer.rstrip('/')}/.well-known/jwks.json"
        self._jwks = PyJWKClient(
            url,
            cache_keys=True,
            lifespan=settings.cache_seconds,
            cooldown_duration=settings.refresh_cooldown_seconds,
        )
        self.jwks_url = url

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def subject_of(self, token: str) -> str:
        """The verified `sub`, or raise.

        Raises rather than returning an empty string on failure. An empty
        subject would flow into the PDS as "no identity", which fails closed
        correctly — but it would record the call as a missing subject rather
        than as a rejected token, and those are different incidents.
        """
        if not self.enabled:
            raise SubjectError("no identity provider is configured")
        if not token or not isinstance(token, str):
            raise SubjectError("subject_token is required and must be a string")

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except Exception as exc:
            raise SubjectError(
                f"could not find a key for this token at {self.jwks_url}: {exc}"
            ) from exc

        options = {"require": ["exp", "sub"], "verify_aud": bool(self.settings.audience)}
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.settings.algorithms),
                issuer=self.settings.issuer,
                audience=self.settings.audience or None,
                leeway=self.settings.leeway_seconds,
                options=options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise SubjectError("subject token has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise SubjectError(
                f"subject token is for a different audience than "
                f"{self.settings.audience!r}: a token minted for another "
                "application at the same issuer is not a subject here"
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise SubjectError(
                f"subject token was not issued by {self.settings.issuer!r}"
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise SubjectError(f"subject token did not verify: {exc}") from exc

        subject = claims.get("sub") or ""
        if not isinstance(subject, str) or not subject:
            raise SubjectError("subject token carries no usable 'sub' claim")
        return subject


def from_env(env: dict) -> OidcSettings:
    raw = env.get("WARRANT_OIDC_ALGORITHMS", "")
    algorithms = tuple(a.strip() for a in raw.split(",") if a.strip()) or DEFAULT_ALGORITHMS
    return OidcSettings(
        issuer=env.get("WARRANT_OIDC_ISSUER", ""),
        audience=env.get("WARRANT_OIDC_AUDIENCE", ""),
        jwks_url=env.get("WARRANT_OIDC_JWKS_URL", ""),
        algorithms=algorithms,
        leeway_seconds=int(env.get("WARRANT_OIDC_LEEWAY", "30")),
        cache_seconds=int(env.get("WARRANT_OIDC_CACHE_SECONDS", "600")),
        refresh_cooldown_seconds=int(env.get("WARRANT_OIDC_REFRESH_COOLDOWN", "30")),
    )
