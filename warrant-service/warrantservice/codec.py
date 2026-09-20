"""The wire format: engine objects to JSON and back.

This lives OUTSIDE `warrant/` on purpose. The engine is a library that signs,
checks and decides; it has no opinion about transport, and a customer
embedding it in a process with no HTTP anywhere should not inherit a codec.
Equally, a service needs parsing that the engine deliberately does not have —
`to_payload()` exists so a body can be hashed and signed, not so it can be
round-tripped.

One rule governs everything here: **the bytes that were signed must be the
bytes that get verified.** So decoding rebuilds the exact same frozen object
the signer built, and re-derives the canonical payload from it rather than
trusting a payload the caller supplied. A service that verified a signature
against caller-supplied JSON, then acted on a separately-parsed object, would
be checking one thing and doing another — the classic signature-confusion shape.

Unknown fields are REJECTED, not ignored. A client that sends `amount_minor`
when the field is `amount_cents`, or that invents `override: true`, has
misunderstood something, and silently dropping it converts a loud integration
bug into a quiet authorization gap.
"""

from __future__ import annotations

from typing import Any

from warrant import (
    ActionAttestation,
    Budget,
    ContractError,
    Decision,
    EvidenceRef,
    Mission,
    SignedAttestation,
    SignedMission,
)


class WireError(ValueError):
    """A request body is not a valid encoding of an engine object."""


def _require(body: Any, *, where: str) -> dict:
    if not isinstance(body, dict):
        raise WireError(f"{where} must be an object, got {type(body).__name__}")
    return body


def _only(body: dict, allowed: set[str], *, where: str) -> None:
    extra = set(body) - allowed
    if extra:
        raise WireError(
            f"{where} has unknown field(s) {sorted(extra)}. Refusing rather than "
            "ignoring them: a field we drop silently is an instruction the "
            "caller believes was honoured"
        )


def _int(body: dict, key: str, *, where: str, default: int = 0) -> int:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WireError(f"{where}.{key} must be an integer, got {value!r}")
    return value


def _str(body: dict, key: str, *, where: str, default: str = "") -> str:
    value = body.get(key, default)
    if not isinstance(value, str):
        raise WireError(f"{where}.{key} must be a string, got {type(value).__name__}")
    return value


def _strs(body: dict, key: str, *, where: str) -> tuple[str, ...]:
    value = body.get(key, [])
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise WireError(f"{where}.{key} must be an array of strings")
    return tuple(value)


# --- Budget -----------------------------------------------------------------

BUDGET_FIELDS = {"amount_minor", "currency", "max_calls"}


def budget_from_wire(body: Any) -> Budget:
    if body is None:
        return Budget()
    body = _require(body, where="budget")
    _only(body, BUDGET_FIELDS, where="budget")
    try:
        return Budget(
            amount_minor=_int(body, "amount_minor", where="budget"),
            currency=_str(body, "currency", where="budget"),
            max_calls=_int(body, "max_calls", where="budget"),
        )
    except ContractError as exc:
        raise WireError(str(exc)) from exc


# --- Mission ----------------------------------------------------------------

MISSION_FIELDS = {
    "mission_id", "subject", "issuer", "instruction_hash", "action_classes",
    "resources", "counterparties", "budget", "not_before", "not_after",
    "max_depth", "issued_at", "supersedes",
}


def mission_to_wire(mission: Mission) -> dict:
    """Exactly `to_payload()`.

    Not a second, prettier shape: the payload IS what the signature covers, so
    any divergence here would hand clients a document they could not verify.
    """
    return mission.to_payload()


def mission_from_wire(body: Any) -> Mission:
    body = _require(body, where="mission")
    _only(body, MISSION_FIELDS, where="mission")
    try:
        return Mission(
            mission_id=_str(body, "mission_id", where="mission"),
            subject=_str(body, "subject", where="mission"),
            issuer=_str(body, "issuer", where="mission"),
            instruction_hash=_str(body, "instruction_hash", where="mission"),
            action_classes=_strs(body, "action_classes", where="mission"),
            resources=_strs(body, "resources", where="mission"),
            counterparties=_strs(body, "counterparties", where="mission"),
            budget=budget_from_wire(body.get("budget")),
            not_before=_int(body, "not_before", where="mission"),
            not_after=_int(body, "not_after", where="mission"),
            max_depth=_int(body, "max_depth", where="mission"),
            issued_at=_int(body, "issued_at", where="mission"),
            supersedes=_str(body, "supersedes", where="mission"),
        )
    except ContractError as exc:
        raise WireError(str(exc)) from exc


def signed_mission_to_wire(signed: SignedMission) -> dict:
    return {
        "mission": mission_to_wire(signed.mission),
        "key_id": signed.key_id,
        "signature": signed.signature,
        "algorithm": signed.algorithm,
    }


def signed_mission_from_wire(body: Any) -> SignedMission:
    body = _require(body, where="signed_mission")
    _only(body, {"mission", "key_id", "signature", "algorithm"}, where="signed_mission")
    try:
        return SignedMission(
            mission=mission_from_wire(body.get("mission")),
            key_id=_str(body, "key_id", where="signed_mission"),
            signature=_str(body, "signature", where="signed_mission"),
            algorithm=_str(body, "algorithm", where="signed_mission", default="Ed25519"),
        )
    except ContractError as exc:
        raise WireError(str(exc)) from exc


# --- Attestation ------------------------------------------------------------

EVIDENCE_FIELDS = {"content_hash", "origin", "locator"}
ATTESTATION_FIELDS = {
    "tree_id", "node_id", "action", "args_hash", "plan_step", "resource",
    "counterparty", "amount_minor", "evidence", "issued_at",
}


def evidence_from_wire(body: Any) -> EvidenceRef:
    body = _require(body, where="evidence[]")
    _only(body, EVIDENCE_FIELDS, where="evidence[]")
    try:
        return EvidenceRef(
            content_hash=_str(body, "content_hash", where="evidence[]"),
            origin=_str(body, "origin", where="evidence[]", default="untrusted"),
            locator=_str(body, "locator", where="evidence[]"),
        )
    except ContractError as exc:
        raise WireError(str(exc)) from exc


def attestation_from_wire(body: Any) -> ActionAttestation:
    body = _require(body, where="attestation")
    _only(body, ATTESTATION_FIELDS, where="attestation")
    evidence = body.get("evidence", [])
    if not isinstance(evidence, list):
        raise WireError("attestation.evidence must be an array")
    try:
        return ActionAttestation(
            tree_id=_str(body, "tree_id", where="attestation"),
            node_id=_str(body, "node_id", where="attestation"),
            action=_str(body, "action", where="attestation"),
            args_hash=_str(body, "args_hash", where="attestation"),
            plan_step=_str(body, "plan_step", where="attestation"),
            resource=_str(body, "resource", where="attestation"),
            counterparty=_str(body, "counterparty", where="attestation"),
            amount_minor=_int(body, "amount_minor", where="attestation"),
            evidence=tuple(evidence_from_wire(e) for e in evidence),
            issued_at=_int(body, "issued_at", where="attestation"),
        )
    except ContractError as exc:
        raise WireError(str(exc)) from exc


def signed_attestation_from_wire(body: Any) -> SignedAttestation:
    body = _require(body, where="signed_attestation")
    _only(body, {"attestation", "key_id", "signature", "algorithm"}, where="signed_attestation")
    try:
        return SignedAttestation(
            attestation=attestation_from_wire(body.get("attestation")),
            key_id=_str(body, "key_id", where="signed_attestation"),
            signature=_str(body, "signature", where="signed_attestation"),
            algorithm=_str(body, "algorithm", where="signed_attestation", default="Ed25519"),
        )
    except ContractError as exc:
        raise WireError(str(exc)) from exc


# --- Decision ---------------------------------------------------------------


def decision_to_wire(decision: Decision) -> dict:
    """`to_payload()` verbatim.

    A decision is the record an auditor is handed, so the shape that leaves
    over HTTP is the shape the engine defined — not a view of it.
    """
    return decision.to_payload()


def decision_from_wire(body: Any) -> Decision:
    """Rebuild a Decision a service returned.

    The client needs this so a remote decision is the same frozen object an
    embedded one is — otherwise every caller would branch on whether its PDS
    happened to be in-process, and the two paths would drift.
    """
    from warrant.contract import CheckResult

    body = _require(body, where="decision")
    checks = body.get("checks") or []
    if not isinstance(checks, list):
        raise WireError("decision.checks must be an array")
    try:
        return Decision(
            effect=_str(body, "effect", where="decision"),
            tree_id=_str(body, "tree_id", where="decision"),
            node_id=_str(body, "node_id", where="decision"),
            mission_id=_str(body, "mission_id", where="decision"),
            checks=tuple(
                CheckResult(
                    check_id=_str(c, "check_id", where="decision.checks[]"),
                    status=_str(c, "status", where="decision.checks[]"),
                    detail=_str(c, "detail", where="decision.checks[]"),
                    on_violation=_str(c, "on_violation", where="decision.checks[]",
                                      default="deny"),
                )
                for c in checks
            ),
            reasons=_strs(body, "reasons", where="decision"),
            step_up_delta=_strs(body, "step_up_delta", where="decision"),
            decided_at=_int(body, "decided_at", where="decision"),
            policy_version=_str(body, "policy_version", where="decision"),
        )
    except ContractError as exc:
        raise WireError(str(exc)) from exc
