"""Value provenance graph: for every leaf value in every tool call's args,
resolve where it came from and how faithfully.

Matching order per Family 2 (prefront-check-families.md): exact -> normalized
-> whitelisted transform -> none. A transform whose result is CLOSE to the
observed value but outside tolerance is recorded as "mutated" rather than
"none" - that distinction is what separates param_mutation (an origin exists
but was altered) from param_provenance (no origin resembles the value at all).

Pure: takes a Session + a transform whitelist and returns a graph. No I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .contract import Session, Step

TRUSTED = "trusted"
SEMI = "semi"
UNTRUSTED = "untrusted"

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass(frozen=True)
class Candidate:
    value: Any
    origin: str  # "user_message" | "tool_result"
    trust: str
    step_seq: Optional[int] = None
    turn_seq: Optional[int] = None
    path: str = ""


@dataclass(frozen=True)
class Origin:
    match: str  # "exact" | "normalized" | "transform" | "mutated" | "none"
    trust: str = ""
    candidate: Optional[Candidate] = None
    transform: str = ""
    delta: Optional[float] = None  # magnitude of deviation, for "mutated"


@dataclass
class ProvenanceGraph:
    origins: dict[tuple[int, str], Origin]

    def get(self, step_seq: int, param_path: str) -> Optional[Origin]:
        return self.origins.get((step_seq, param_path))

    def params_for(self, step_seq: int) -> dict[str, Origin]:
        return {p: o for (s, p), o in self.origins.items() if s == step_seq}


def flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.extend(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(flatten(v, f"{prefix}[{i}]"))
    elif value is not None and value != "":
        out.append((prefix, value))
    return out


def _normalize(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if isinstance(value, str):
        s = value.strip().lower()
        m = _NUM_RE.fullmatch(s.replace("$", "").replace("%", "").strip())
        if m:
            try:
                return round(float(m.group(0).replace(",", "")), 6)
            except ValueError:
                pass
        return re.sub(r"\s+", " ", s)
    return value


def is_numeric_like(value: Any) -> bool:
    """True if `value` has an extractable numeric component. The shape
    param_provenance uses to tell a factual quantity/identifier claim (an
    account ID, an amount, a rate - prefront-check-families.md's own examples
    for that check are all numeric-shaped) from a categorical judgment
    (an approval decision, a notice kind) with no such component: only the
    former needs a traceable origin to not be "fabricated" - see
    param_provenance.py."""
    return _numeric(value) is not None


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        for m in _NUM_RE.finditer(value.replace(",", "")):
            try:
                return float(m.group(0))
            except ValueError:
                continue
    return None


def _step_trust(step: Step) -> str:
    tc = (step.trust_class or "").lower()
    if tc in (TRUSTED, SEMI, UNTRUSTED):
        return tc
    return TRUSTED  # a verified tool result with no declared trust_class defaults to trusted


def _candidates_before(session: Session, step: Step) -> list[Candidate]:
    out: list[Candidate] = []
    for s in session.steps:
        if s.seq >= step.seq:
            break
        trust = _step_trust(s)
        for path, v in flatten(s.args, "arg"):
            out.append(Candidate(value=v, origin="tool_result", trust=trust, step_seq=s.seq, path=path))
        for path, v in flatten(s.result, "result"):
            out.append(Candidate(value=v, origin="tool_result", trust=trust, step_seq=s.seq, path=path))
    turn_limit = step.turn_seq if step.turn_seq is not None else len(session.turns)
    for t in session.turns:
        if t.seq > (turn_limit if turn_limit is not None else t.seq):
            break
        if t.user_message:
            out.append(Candidate(value=t.user_message, origin="user_message", trust=SEMI, turn_seq=t.seq))
            # Every number the user typed is its own candidate, so a numeric
            # arg is compared against each one - not just the FIRST number in
            # the message, which is what _numeric(whole message) yielded (a
            # message "change 7001 to $30,500" only ever offered 7001 as the
            # origin for an amount arg; caught live on a distorted-amount
            # scenario that could therefore never be a near-miss of 30500).
            for i, tok in enumerate(_NUM_RE.finditer(t.user_message.replace(",", ""))):
                try:
                    out.append(Candidate(value=float(tok.group(0)), origin="user_number", trust=SEMI,
                                         turn_seq=t.seq, path=f"message#{i}"))
                except ValueError:
                    continue
    return out


def _find_exact(value: Any, candidates: list[Candidate]) -> Optional[Candidate]:
    for c in candidates:
        if c.origin in ("tool_result", "user_number") and c.value == value:
            return c
        if c.origin == "user_message" and isinstance(value, str) and value and value in c.value:
            return c
    return None


def _find_normalized(value: Any, candidates: list[Candidate]) -> Optional[Candidate]:
    nv = _normalize(value)
    for c in candidates:
        if c.origin in ("tool_result", "user_number") and _normalize(c.value) == nv:
            return c
        if c.origin == "user_message":
            if isinstance(nv, str) and nv and nv in _normalize(c.value):
                return c
            if isinstance(nv, float):
                for tok in _NUM_RE.finditer(c.value.replace(",", "")):
                    try:
                        if abs(float(tok.group(0)) - nv) < 1e-6:
                            return c
                    except ValueError:
                        continue
    return None


# Whitelisted numeric transforms: (name, fn). Each takes a candidate numeric
# value and returns the derived value a legitimate agent could have computed.
_TRANSFORMS = (
    ("round", lambda x: round(x)),
    ("round2", lambda x: round(x, 2)),
    ("cents_to_dollars", lambda x: x / 100.0),
    ("dollars_to_cents", lambda x: x * 100.0),
    ("percent_to_fraction", lambda x: x / 100.0),
    ("fraction_to_percent", lambda x: x * 100.0),
)


def _within_tolerance(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b), 1.0))


def _find_transform(value: Any, candidates: list[Candidate], abs_tol: float,
                    rel_tol: float) -> tuple[Optional[Candidate], str, Optional[float]]:
    """Returns (candidate, transform_name, delta) - delta set only on a near-miss ("mutated").

    The near-miss ("best_miss") bucket exists for param_mutation's real use
    case - a value that's CLOSE to a transform of some candidate but outside
    the tight exact-match tolerance (a genuine rounding/unit slip). Its
    acceptance window scales off the caller's own rel_tol rather than a fixed
    constant: a flat 50% window (this used to be hardcoded) means any two
    same-order-of-magnitude numeric IDs are "near" each other by chance - a
    live end-to-end run against a real fixture caught this for real (a
    fabricated numeric identifier matched an UNRELATED identifier from
    earlier in the session as a "mutated round()" origin, purely because they
    happened to be the same order of magnitude, masking param_provenance's
    fabrication finding entirely). 40x rel_tol keeps the near-miss window
    meaningfully tighter than "same ballpark" while still wide enough to
    catch a real typo-class slip (rel_tol's default of 0.005 -> a 20%
    near-miss window: 35,000 typed for 30,500 is 12.9% off and must be
    caught, an unrelated id ~30% away must not), floored at 5% so a very
    tight configured rel_tol doesn't make near-miss reporting useless.
    """
    target = _numeric(value)
    if target is None:
        return None, "", None
    near_miss_limit = max(rel_tol * 40, 0.05)
    best_miss: tuple[Optional[Candidate], str, Optional[float]] = (None, "", None)
    for c in candidates:
        base = _numeric(c.value)
        if base is None:
            continue
        for name, fn in _TRANSFORMS:
            try:
                derived = fn(base)
            except (ZeroDivisionError, OverflowError):
                continue
            if _within_tolerance(derived, target, abs_tol, rel_tol):
                return c, name, None
            delta = abs(derived - target)
            rel = delta / max(abs(target), 1.0)
            if rel < near_miss_limit and (best_miss[2] is None or delta < best_miss[2]):
                best_miss = (c, name, delta)
    if best_miss[0] is not None:
        return best_miss
    return None, "", None


def build(session: Session, abs_tol: float, rel_tol: float) -> ProvenanceGraph:
    origins: dict[tuple[int, str], Origin] = {}
    for step in session.steps:
        candidates = _candidates_before(session, step)
        for path, value in flatten(step.args):
            # Trusted-layer identity (session_id/user_id) is stamped by config,
            # never typed by the agent - it never needs to trace to a message or
            # a prior result. See CLAUDE.md: "Identity is trusted-layer only".
            if isinstance(value, str) and value and value in (session.session_id, session.user_id):
                origins[(step.seq, path)] = Origin(match="exact", trust=TRUSTED, candidate=None)
                continue
            if isinstance(value, bool):
                origins[(step.seq, path)] = Origin(match="exact", trust=TRUSTED, candidate=None)
                continue
            exact = _find_exact(value, candidates)
            if exact is not None:
                origins[(step.seq, path)] = Origin(match="exact", trust=exact.trust, candidate=exact)
                continue
            norm = _find_normalized(value, candidates)
            if norm is not None:
                origins[(step.seq, path)] = Origin(match="normalized", trust=norm.trust, candidate=norm)
                continue
            cand, transform, delta = _find_transform(value, candidates, abs_tol, rel_tol)
            if cand is not None and delta is None:
                origins[(step.seq, path)] = Origin(match="transform", trust=cand.trust, candidate=cand, transform=transform)
            elif cand is not None:
                origins[(step.seq, path)] = Origin(match="mutated", trust=cand.trust, candidate=cand,
                                                    transform=transform, delta=delta)
            else:
                origins[(step.seq, path)] = Origin(match="none")
    return ProvenanceGraph(origins=origins)
