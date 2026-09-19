"""Mission Authority: issuance, supersession, revocation, offline verification.

Supersession is the case the spec names among its 51 scenarios and the one
most easily got wrong: an earlier Mission's signature stays valid forever, so
something has to remember that the user replaced it.
"""

from __future__ import annotations

import pytest

from conftest import NOW
from warrant import Budget, ContractError, KeySet, MissionAuthority, SigningKey, VerificationError
from warrant.canonical import instruction_hash


@pytest.fixture
def ma() -> MissionAuthority:
    return MissionAuthority("authority.test", SigningKey.generate("ma-1"))


def issue(ma, mission_id="m1", instruction="Do the quarterly tidy-up.", **kw):
    return ma.issue(mission_id=mission_id, subject="user-1", instruction=instruction, **kw)


# --- issuance ---------------------------------------------------------------


def test_an_issued_mission_verifies_under_the_published_keys(ma):
    signed = issue(ma)
    ma.verify(signed)
    ma.verify(signed, keys=KeySet.from_jwks(ma.jwks()))  # as an offline verifier would


def test_the_instruction_is_hashed_here_not_accepted_as_a_hash(ma):
    """The consent screen showed the user those words. A caller supplying a
    hash could make the thing signed diverge from the thing displayed."""
    signed = issue(ma, instruction="Pay the outstanding invoice.")
    assert signed.mission.instruction_hash == instruction_hash("Pay the outstanding invoice.")


def test_a_mission_with_no_instruction_is_refused(ma):
    with pytest.raises(ContractError, match="not consent"):
        issue(ma, instruction="")


def test_a_mission_id_cannot_be_reused(ma):
    """Two different consents sharing an id are indistinguishable in the
    evidence chain."""
    issue(ma)
    with pytest.raises(ContractError, match="already been issued"):
        issue(ma)


def test_tampering_with_any_field_breaks_the_signature(ma):
    from dataclasses import replace

    signed = issue(ma, budget=Budget(100, "USD"))
    widened = replace(signed, mission=replace(signed.mission, budget=Budget(1_000_000, "USD")))
    with pytest.raises(VerificationError):
        ma.verify(widened)


def test_a_mission_from_another_issuer_is_refused(ma):
    other = MissionAuthority("authority.other", SigningKey.generate("ma-9"))
    with pytest.raises(VerificationError, match="names issuer"):
        ma.verify(issue(other))


# --- supersession -----------------------------------------------------------


def test_a_superseded_mission_stops_verifying_even_though_it_is_genuine(ma):
    """The signature does not expire because the user changed their mind, so
    the Authority has to remember that they did."""
    first = issue(ma, "m1")
    ma.verify(first)

    issue(ma, "m2", supersedes="m1")

    with pytest.raises(VerificationError, match="superseded by 'm2'"):
        ma.verify(first)


def test_the_replacement_mission_is_current(ma):
    issue(ma, "m1")
    second = issue(ma, "m2", supersedes="m1")
    ma.verify(second)
    assert ma.is_current("m2")
    assert not ma.is_current("m1")
    assert ma.superseded_by("m1") == "m2"


def test_a_chain_of_consent_cannot_fork(ma):
    """Forking would leave two live permissions where the user believes there
    is one."""
    issue(ma, "m1")
    issue(ma, "m2", supersedes="m1")
    with pytest.raises(ContractError, match="already superseded"):
        issue(ma, "m3", supersedes="m1")


def test_a_chain_can_extend_indefinitely(ma):
    issue(ma, "m1")
    issue(ma, "m2", supersedes="m1")
    third = issue(ma, "m3", supersedes="m2")
    ma.verify(third)
    assert not ma.is_current("m2")


def test_superseding_an_unknown_mission_is_refused(ma):
    with pytest.raises(ContractError, match="unknown Mission"):
        issue(ma, "m2", supersedes="never-existed")


# --- revocation -------------------------------------------------------------


def test_a_revoked_mission_stops_verifying(ma):
    signed = issue(ma)
    ma.revoke("m1", NOW)
    with pytest.raises(VerificationError, match="revoked"):
        ma.verify(signed)


def test_revocation_is_idempotent_and_keeps_the_first_time(ma):
    issue(ma)
    ma.revoke("m1", 100)
    ma.revoke("m1", 900)
    with pytest.raises(VerificationError, match="revoked at 100"):
        ma.verify(ma.get("m1"))


# --- the verifier a third party runs ----------------------------------------


def test_signature_alone_is_not_enough_and_the_test_says_why(ma):
    """A resource server that checks only Ed25519 would accept a superseded
    Mission. The published verifiers have to carry this check too — which is
    why `verify` does both rather than leaving supersession to a caller."""
    first = issue(ma, "m1")
    issue(ma, "m2", supersedes="m1")

    # The raw signature still verifies; that is exactly the problem.
    KeySet.from_jwks(ma.jwks()).verify(
        first.mission.to_payload(), first.key_id, first.signature
    )
    with pytest.raises(VerificationError):
        ma.verify(first)
