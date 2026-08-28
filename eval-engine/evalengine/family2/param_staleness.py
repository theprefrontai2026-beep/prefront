"""Stale parameter: value sourced from a step whose data was superseded
before this call used the old value.

Two ways a value can be superseded, both checked:
  - a later call to the SAME tool returns a different value at the same
    result path (the straightforward "re-read disagrees" case).
  - an intervening WRITE step's own args touch a field of the same name
    with a different value. This matters independently of the re-read case:
    a subject app that doesn't actually persist writes (a sandboxed demo
    that rolls every write back so its seed data stays stable, or any app
    whose write path has a bug) means a later read of the same tool will
    keep showing the PRE-write value forever, so the re-read signal alone
    can never fire even though the agent unambiguously issued a change to
    that field. The write's own args are the ground truth for "the agent
    believes this changed," independent of whether a subsequent read
    happens to confirm it.

Applicable only when the originating tool was actually re-invoked, or a
write touched the same-named field, between the source step and this use
(Hard Rule 16) - otherwise there is nothing to be stale relative to.
"""

from __future__ import annotations

from ..contract import CheckContext, Evidence, Session, Verdict
from ..provenance import flatten

CHECK_ID = "param_staleness"


def _leaf_name(path: str) -> str:
    return path.rsplit(".", 1)[-1].split("[")[0]


def evaluate(session: Session, ctx: CheckContext) -> list[Verdict]:
    by_seq = {s.seq: s for s in session.steps}
    out: list[Verdict] = []
    for step in session.steps:
        for path, origin in ctx.provenance.params_for(step.seq).items():
            c = origin.candidate
            if c is None or c.origin != "tool_result" or c.step_seq is None:
                continue
            src = by_seq.get(c.step_seq)
            if src is None:
                continue
            leaf = _leaf_name(c.path)
            between = [s for s in session.steps if src.seq < s.seq < step.seq]
            same_tool_rereads = [s for s in between if s.tool_name == src.tool_name]
            writes = [s for s in between if s.side_effect == "write"]
            if not same_tool_rereads and not writes:
                continue  # nothing happened between source and use: not applicable

            stale = False
            newest = None
            how = ""
            for r in same_tool_rereads:
                for rpath, rvalue in flatten(r.result, "result"):
                    if rpath == c.path and rvalue != c.value:
                        stale, newest, how = True, r, "re-read"
                        break
                if stale:
                    break
            if not stale:
                for r in writes:
                    for rpath, rvalue in flatten(r.args, "arg"):
                        if _leaf_name(rpath) == leaf and rvalue != c.value:
                            stale, newest, how = True, r, "write"
                            break
                    if stale:
                        break

            span_ids = (src.span_id, step.span_id) if newest is None else (src.span_id, newest.span_id, step.span_id)
            if stale:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="violated", effect="approval_required",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=span_ids, excerpt=f"{step.tool_name}.{path}"),
                    detail=(f"arg '{path}' on {step.tool_name} (step {step.seq}) reuses step {src.seq}'s "
                            f"{c.path}, superseded by a {how} at step {newest.seq if newest else '?'}"),
                ))
            else:
                out.append(Verdict(
                    check_id=CHECK_ID, family="family2", status="satisfied", effect="allow",
                    session_id=session.session_id,
                    evidence=Evidence(span_ids=span_ids, excerpt=f"{step.tool_name}.{path}"),
                    detail=f"arg '{path}' confirmed unchanged across re-invocation of {src.tool_name}",
                ))
    return out
