"""Mission Authority: sign and store Missions, publish keys, honour supersession.

The Authority is the only component that can widen an agent's permissions, and
it does so only by signing something a human approved. Everything else in the
enforcement plane can narrow.

Supersession is the part worth reading twice. A user who changes their mind
mid-task gets a NEW Mission that names the old one in `supersedes`, and the old
id is retired at that moment. Without that retirement a captured earlier
Mission would remain a perfectly valid, correctly-signed permission — the
signature does not expire just because the user changed their mind, so
something has to remember that they did. `verify()` is where that memory is
enforced, which means a resource server that checks only the signature is
missing a control; the spec's published verifiers therefore have to carry the
supersession check too, not just Ed25519.
"""

from __future__ import annotations

import threading
from typing import Optional

from .canonical import instruction_hash
from .contract import Budget, ContractError, Mission, SignedMission
from .signing import KeySet, SigningKey, VerificationError


class MissionAuthority:
    """Signs Missions and remembers which ones are still current."""

    def __init__(self, issuer: str, key: SigningKey) -> None:
        if not issuer:
            raise ContractError("a Mission Authority must have an issuer id")
        self.issuer = issuer
        self._key = key
        self._lock = threading.RLock()
        self._issued: dict[str, SignedMission] = {}
        self._superseded: dict[str, str] = {}  # old id -> the id that replaced it
        self._revoked: dict[str, int] = {}

    # -- publication ------------------------------------------------------

    def key_set(self) -> KeySet:
        """The public keys, as any resource server should hold them."""
        return KeySet([self._key.verify_key()])

    def jwks(self) -> dict:
        return self.key_set().to_jwks()

    # -- issuance ---------------------------------------------------------

    def issue(
        self,
        *,
        mission_id: str,
        subject: str,
        instruction: str,
        action_classes: tuple[str, ...] = (),
        resources: tuple[str, ...] = (),
        counterparties: tuple[str, ...] = (),
        budget: Optional[Budget] = None,
        not_before: int = 0,
        not_after: int = 0,
        max_depth: int = 0,
        issued_at: int = 0,
        supersedes: str = "",
    ) -> SignedMission:
        """Sign one Mission.

        Takes the user's INSTRUCTION as text and hashes it here, rather than
        accepting a hash. The consent screen showed the user those words, and
        letting a caller supply a hash would let the thing signed diverge from
        the thing displayed — which is the one lie this object exists to
        prevent.
        """
        if not instruction:
            raise ContractError(
                "a Mission needs the user's instruction: an approval with no "
                "statement of what was approved is not consent"
            )
        mission = Mission(
            mission_id=mission_id,
            subject=subject,
            issuer=self.issuer,
            instruction_hash=instruction_hash(instruction),
            action_classes=tuple(action_classes),
            resources=tuple(resources),
            counterparties=tuple(counterparties),
            budget=budget or Budget(),
            not_before=not_before,
            not_after=not_after,
            max_depth=max_depth,
            issued_at=issued_at,
            supersedes=supersedes,
        )
        with self._lock:
            if mission_id in self._issued:
                raise ContractError(
                    f"Mission id {mission_id!r} has already been issued: reusing an "
                    "id would make two different consents indistinguishable in the "
                    "evidence chain"
                )
            if supersedes:
                if supersedes not in self._issued:
                    raise ContractError(
                        f"cannot supersede unknown Mission {supersedes!r}"
                    )
                if supersedes in self._superseded:
                    raise ContractError(
                        f"Mission {supersedes!r} was already superseded by "
                        f"{self._superseded[supersedes]!r}: a chain of consent has "
                        "one head, and forking it would leave two live permissions"
                    )
                self._superseded[supersedes] = mission_id

            signed = SignedMission(
                mission=mission,
                key_id=self._key.key_id,
                signature=self._key.sign(mission.to_payload()),
            )
            self._issued[mission_id] = signed
            return signed

    # -- lifecycle --------------------------------------------------------

    def revoke(self, mission_id: str, at: int) -> None:
        """Retire a Mission without replacing it (the user cancelled)."""
        with self._lock:
            self._revoked.setdefault(mission_id, at)

    def is_current(self, mission_id: str) -> bool:
        with self._lock:
            return mission_id not in self._superseded and mission_id not in self._revoked

    def superseded_by(self, mission_id: str) -> str:
        with self._lock:
            return self._superseded.get(mission_id, "")

    # -- verification -----------------------------------------------------

    def verify(self, signed: SignedMission, keys: Optional[KeySet] = None) -> None:
        """Raise unless this Mission is authentic AND still current.

        `keys` lets a resource server pass its own published set, so this same
        function is what an offline verifier runs — the Authority is not
        special-cased into its own code path, which is how the two would drift.
        """
        mission = signed.mission
        if mission.issuer != self.issuer:
            raise VerificationError(
                f"Mission {mission.mission_id!r} names issuer {mission.issuer!r}, "
                f"not {self.issuer!r}"
            )
        (keys or self.key_set()).verify(
            mission.to_payload(), signed.key_id, signed.signature, signed.algorithm
        )
        with self._lock:
            replacement = self._superseded.get(mission.mission_id)
            revoked_at = self._revoked.get(mission.mission_id)
        if replacement:
            raise VerificationError(
                f"Mission {mission.mission_id!r} was superseded by {replacement!r}: "
                "the signature is genuine, but the user has since replaced this "
                "consent"
            )
        if revoked_at is not None:
            raise VerificationError(
                f"Mission {mission.mission_id!r} was revoked at {revoked_at}"
            )

    def get(self, mission_id: str) -> SignedMission:
        with self._lock:
            signed = self._issued.get(mission_id)
        if signed is None:
            raise VerificationError(f"unknown Mission {mission_id!r}")
        return signed
