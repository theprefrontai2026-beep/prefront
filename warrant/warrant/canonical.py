"""Canonical serialization and hashing — the one thing both zones must agree on.

The spec splits the deployment so that the CONTROL zone holds "identity,
decisions and hashes only" while the DATA zone holds payloads, and then makes
a promise on top of that split: a bank "can run the control zone in its own
account with hashes only, keep every payload inside its data zone, and still
hand an auditor one chain of evidence because the hash in the control zone
matches the payload in the data zone."

That equality IS the product. It holds only if two processes, in two zones,
possibly written in two languages, hash the same logical object to the same
bytes. So canonicalization here is not a formatting preference — it is the
integrity boundary, and every rule below exists because breaking it silently
converts the evidence chain into a chain of unrelated numbers.

The rules, in the order they bite:

1. **Sorted keys, no insignificant whitespace, UTF-8, no NaN/Infinity.** The
   obvious part. `allow_nan=False` matters because Python's json module emits
   bare `NaN`/`Infinity` by default, which is not JSON at all and which no
   other language's parser will accept — the hash would be computed over bytes
   the data zone could never reproduce.

2. **Integral floats are written as integers.** `1.0` and `1` are the same
   budget, and a customer whose SDK happens to hand us one rather than the
   other must not get a different `args_hash` for the same call.

3. **Every digest is DOMAIN-SEPARATED by a type tag.** A Mission's hash and an
   argument bag's hash must never be able to collide, even if their canonical
   JSON is byte-identical. Without separation an attacker who can choose an
   argument bag can choose bytes that hash to a value the system will later
   accept as a Mission id. This is cheap to do and unfixable after the fact,
   because hashes are persisted and quoted to auditors.

4. **Unordered and unrepresentable types are refused, never coerced.** A
   Python `set` has no stable iteration order across processes, so hashing one
   would produce a value that fails to reproduce roughly at random — the worst
   possible failure mode for evidence, since it looks like tampering. The
   caller sorts it into a list and tells us what order it meant.

One residual risk, stated rather than hidden: non-integral floats are written
with Python's shortest round-trip repr. Go's `strconv` shortest form and
JavaScript's `Number.prototype.toString` agree with it for IEEE-754 doubles,
so cross-language hashing holds in practice — but money should be carried as
integer minor units anyway (`budget.amount_minor`), which is why `Budget`
refuses floats outright rather than relying on this paragraph.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Domain-separation tags. Each is the ASCII prefix mixed into the digest before
# the canonical bytes, so the same bytes under two tags give two hashes.
#
# These strings are part of the WIRE FORMAT: every published verifier, in every
# language, hardcodes them. Changing one does not "re-tag" anything — it
# invalidates every hash of that type ever persisted, in a way that reads as
# forgery rather than as a version change. Add a new tag instead.
TAG_MISSION = "warrant.mission.v1"
TAG_INSTRUCTION = "warrant.instruction.v1"
TAG_ARGS = "warrant.args.v1"
TAG_CONTENT = "warrant.content.v1"
TAG_ATTESTATION = "warrant.attestation.v1"
TAG_DECISION = "warrant.decision.v1"

_ALLOWED_SCALARS = (str, int, float, bool, type(None))


class CanonicalizationError(ValueError):
    """A value cannot be canonicalized, so it must not be hashed or signed.

    Always raised, never worked around with a best-effort coercion: a value we
    had to guess at is a value the other zone will guess at differently.
    """


def _normalize(value: Any, *, path: str = "$") -> Any:
    """Recursively convert `value` into the subset of JSON we will hash.

    `path` is threaded purely so the error message names the offending field —
    a canonicalization failure surfaces at sign time, often deep inside a
    customer's argument bag, and "unhashable value at $.args.attachments[0]"
    is the difference between a one-minute fix and a support ticket.
    """
    # bool before int: `isinstance(True, int)` is True in Python, and a bool
    # that fell through to the int branch would be hashed as 1/0 — same digest
    # for `true` and `1`, which are different JSON values to every other
    # language's parser.
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        # Rule 1: these are not JSON. Catch them here with a message that says
        # which field, rather than letting json.dumps raise about the whole bag.
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalizationError(
                f"non-finite float at {path}: NaN and Infinity are not JSON and "
                "cannot appear in a hashed object"
            )
        # Rule 2: 1.0 and 1 are one value.
        if value.is_integer():
            return int(value)
        return value

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            # Non-string keys would be stringified by json.dumps (`1` -> "1"),
            # which silently merges {1: a, "1": b} into one entry. Refuse.
            if not isinstance(key, str):
                raise CanonicalizationError(
                    f"non-string key {key!r} at {path}: object keys must be "
                    "strings, since stringifying them can merge two distinct keys"
                )
            out[key] = _normalize(item, path=f"{path}.{key}")
        return out

    # Tuples are accepted as lists: they are the repo's default for a frozen
    # dataclass field, and their ORDER is meaningful and preserved, so unlike a
    # set there is nothing to guess.
    if isinstance(value, (list, tuple)):
        return [_normalize(item, path=f"{path}[{i}]") for i, item in enumerate(value)]

    # Rule 4. Named explicitly because `set` is the one a caller reaches for by
    # reflex when a field means "a collection of counterparties".
    if isinstance(value, (set, frozenset)):
        raise CanonicalizationError(
            f"set at {path}: iteration order is not stable across processes, so "
            "hashing one produces evidence that fails to reproduce at random. "
            "Sort it into a list at the call site and state the order you meant"
        )

    raise CanonicalizationError(
        f"unhashable value of type {type(value).__name__} at {path}: only "
        f"{', '.join(t.__name__ for t in _ALLOWED_SCALARS)}, dict and list/tuple "
        "can appear in a hashed object"
    )


def canonical_json(value: Any) -> bytes:
    """The exact bytes that get hashed and signed.

    Exposed (rather than kept private to `digest`) because a verifier in
    another language needs a fixture to test itself against, and because a
    debugging session that cannot see these bytes cannot explain a hash
    mismatch.
    """
    normalized = _normalize(value)
    text = json.dumps(
        normalized,
        sort_keys=True,          # rule 1: key order is not information
        separators=(",", ":"),   # rule 1: no insignificant whitespace
        ensure_ascii=False,      # emit real UTF-8 rather than \uXXXX escapes
        allow_nan=False,         # rule 1: belt as well as braces, see _normalize
    )
    return text.encode("utf-8")


def digest(tag: str, value: Any) -> str:
    """Domain-separated SHA-256 of `value`, as lowercase hex.

    The tag is mixed in with a NUL separator rather than plain concatenation:
    without a separator, tag "ab" + bytes "c" and tag "a" + bytes "bc" are the
    same input, which quietly re-opens the collision the tag exists to close.
    NUL cannot occur in the tag (they are ASCII constants) and is escaped by
    JSON inside the payload, so it cannot occur in the body either.
    """
    if not tag:
        raise CanonicalizationError("refusing to hash without a domain-separation tag")
    h = hashlib.sha256()
    h.update(tag.encode("utf-8"))
    h.update(b"\x00")
    h.update(canonical_json(value))
    return h.hexdigest()


def args_hash(args: dict[str, Any]) -> str:
    """Hash of a tool call's argument bag — the `args_hash` on an attestation.

    This is the value that lets the control zone state what a call did without
    holding what it did to: the PDS compares the hash the model attested to
    against the hash of the arguments the gateway actually received, and the
    evidence store keys the full payload on the same value.
    """
    return digest(TAG_ARGS, args)


def instruction_hash(instruction: str) -> str:
    """Hash of the user's own words, as shown on the consent screen.

    Hashed rather than stored in the control zone because the instruction is
    user content — it is exactly the sort of text that carries a name, an
    account number or a diagnosis, and the whole point of the zone split is
    that such text never leaves the tenant.
    """
    return digest(TAG_INSTRUCTION, instruction)


def content_hash(content: str) -> str:
    """Hash of one piece of evidence the model acted on (a retrieved document,
    a prior tool result, a prompt constant).

    The runtime ships the underlying CONTENT to the trace, not just this hash —
    the judgement plane computes provenance independently, and it cannot do
    that from a digest. The hash is what the attestation carries so that the
    enforcement plane can check, on the path and in constant time, that the
    content judged later is the content acted on now.
    """
    return digest(TAG_CONTENT, content)
