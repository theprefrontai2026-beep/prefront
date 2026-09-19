"""Canonicalization and signatures — the foundation the evidence chain rests on.

The spec's promise is that "the hash in the control zone matches the payload in
the data zone". Everything here tests one half of that: that the same logical
object always produces the same bytes, and that a different one never does.
"""

from __future__ import annotations

import pytest

from warrant import KeySet, SigningKey, VerificationError, args_hash, canonical_json, digest
from warrant.canonical import (
    TAG_ARGS,
    TAG_MISSION,
    CanonicalizationError,
    content_hash,
    instruction_hash,
)
from warrant.signing import SigningError, VerifyKey, b64u_decode, b64u_encode


# --- stability --------------------------------------------------------------


def test_key_order_does_not_change_the_bytes():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_integral_floats_and_ints_are_the_same_value():
    """`1.0` and `1` are one budget. A customer's SDK choosing one spelling
    must not produce a different args_hash for the same call."""
    assert args_hash({"n": 1}) == args_hash({"n": 1.0})


def test_true_is_not_one():
    """bool is an int in Python; hashing `True` as 1 would make two distinct
    JSON values indistinguishable to every other language's parser."""
    assert args_hash({"n": True}) != args_hash({"n": 1})


def test_nesting_is_canonicalized_all_the_way_down():
    assert canonical_json({"a": [{"y": 2.0, "x": 1}]}) == canonical_json({"a": [{"x": 1, "y": 2}]})


def test_unicode_is_emitted_as_utf8_not_escapes():
    assert canonical_json({"s": "café"}) == b'{"s":"caf\xc3\xa9"}'


# --- domain separation ------------------------------------------------------


def test_the_same_bytes_under_two_tags_give_two_hashes():
    """Without separation, an attacker who can choose an argument bag can choose
    bytes the system will later accept as a Mission id."""
    assert digest(TAG_ARGS, {"x": 1}) != digest(TAG_MISSION, {"x": 1})


def test_every_helper_is_separated_from_every_other():
    same = "x"
    assert len({instruction_hash(same), content_hash(same), args_hash({"a": same})}) == 3


def test_hashing_without_a_tag_is_refused():
    with pytest.raises(CanonicalizationError):
        digest("", {"x": 1})


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value, because",
    [
        ({"s": {1, 2}}, "a set has no stable iteration order across processes"),
        ({1: "a"}, "a non-string key would be stringified and could merge two keys"),
        ({"f": float("nan")}, "NaN is not JSON and no other parser would accept it"),
        ({"f": float("inf")}, "Infinity is not JSON"),
        ({"o": object()}, "an arbitrary object has no canonical form"),
    ],
)
def test_unrepresentable_values_are_refused_never_coerced(value, because):
    """A value we had to guess at is a value the other zone will guess at
    differently — and evidence that fails to reproduce reads as tampering."""
    with pytest.raises(CanonicalizationError):
        canonical_json(value)


def test_the_error_names_the_offending_field():
    """A canonicalization failure surfaces deep inside a customer's argument
    bag; the path is the difference between a one-minute fix and a ticket."""
    with pytest.raises(CanonicalizationError, match=r"\$\.outer\.inner\[1\]"):
        canonical_json({"outer": {"inner": ["ok", {1, 2}]}})


# --- signatures -------------------------------------------------------------


def test_a_signature_verifies_and_tampering_breaks_it():
    key = SigningKey.generate("k1")
    sig = key.sign({"a": 1})
    key.verify_key().verify({"a": 1}, sig)
    with pytest.raises(VerificationError):
        key.verify_key().verify({"a": 2}, sig)


def test_verification_raises_rather_than_returning_a_bool():
    """`if verify(...)` written as `verify(...)` in a hurry would be a silent
    accept-everything — the single worst bug this module could have."""
    key = SigningKey.generate("k1")
    assert key.verify_key().verify({"a": 1}, key.sign({"a": 1})) is None


def test_a_published_key_set_verifies_offline():
    """An air-gapped resource server holds a JWKS and nothing else."""
    key = SigningKey.generate("ma-1")
    sig = key.sign({"claim": "x"})

    jwks = KeySet([key.verify_key()]).to_jwks()
    offline = KeySet.from_jwks(jwks)  # no access to the signer at all

    offline.verify({"claim": "x"}, "ma-1", sig)


def test_the_algorithm_field_is_checked_never_dispatched_on():
    """Algorithm agility in a signed-token format is where alg-confusion lives."""
    key = SigningKey.generate("k1")
    ks = KeySet([key.verify_key()])
    with pytest.raises(VerificationError, match="Ed25519 only"):
        ks.verify({"a": 1}, "k1", key.sign({"a": 1}), algorithm="HS256")


def test_an_unknown_key_id_is_an_error_not_a_search():
    """Trying every key until one works turns a real signal into a success
    under a key nobody expected."""
    key = SigningKey.generate("k1")
    ks = KeySet([key.verify_key()])
    with pytest.raises(VerificationError, match="refusing to guess"):
        ks.verify({"a": 1}, "k2", key.sign({"a": 1}))


def test_a_kid_cannot_be_rebound_to_new_key_material():
    """Rotation adds a kid. Rebinding one would make every object signed under
    the old key start reading as a forgery, with no explanation."""
    ks = KeySet([SigningKey.generate("k1").verify_key()])
    with pytest.raises(SigningError, match="already published"):
        ks.add(SigningKey.generate("k1").verify_key())


def test_two_keys_can_be_live_at_once_during_rotation():
    old, new = SigningKey.generate("k1"), SigningKey.generate("k2")
    ks = KeySet([old.verify_key(), new.verify_key()])
    ks.verify({"a": 1}, "k1", old.sign({"a": 1}))
    ks.verify({"a": 1}, "k2", new.sign({"a": 1}))


def test_the_jwks_document_is_byte_stable_across_rebuilds():
    """It gets cached, diffed and checked into customer config; a set that
    reorders itself on every boot looks like a key change."""
    keys = [SigningKey.generate(k).verify_key() for k in ("kb", "ka", "kc")]
    assert KeySet(keys).to_jwks() == KeySet(list(reversed(keys))).to_jwks()


def test_a_jwk_of_the_wrong_key_type_is_refused():
    with pytest.raises(VerificationError, match="Ed25519 only"):
        VerifyKey.from_jwk({"kty": "RSA", "crv": "P-256", "kid": "x", "x": "AA"})


def test_base64url_roundtrips_and_rejects_garbage():
    assert b64u_decode(b64u_encode(b"\x00\xff~?")) == b"\x00\xff~?"
    with pytest.raises(VerificationError):
        b64u_decode("not valid base64!!!")


def test_a_private_key_survives_a_vault_roundtrip():
    key = SigningKey.generate("k1")
    restored = SigningKey.from_raw("k1", key.private_bytes())
    key.verify_key().verify({"a": 1}, restored.sign({"a": 1}))
