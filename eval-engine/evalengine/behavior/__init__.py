"""Behavioural aggregates over observed traces — the input to intent mining.

`intent_learning_design.md` phase L1. For a customer with no policy document,
their traces already contain most of an intent catalog: every session records
the tool called, the caller's role and channel, the arguments, the columns
returned, the row count, the side effect and the order it happened in.

THIS PACKAGE ONLY COUNTS. Nothing here guesses, names or infers — every number
is an aggregate a reviewer could reproduce with a SQL query, which is what
makes it auditable. Synthesis (naming a candidate intent, and reverse-engineering
the policy that appears to govern it) is a separate, LLM-assisted, design-time
step in semantic-layer, and it consumes what this package emits.

Two constraints this package exists under, both from the design:

FREQUENCY IS NOT LEGITIMACY. Mining observed behaviour learns what an agent
DID, never what it should have done. An agent that leaked SSNs for six months
teaches a naive miner that SSN access is normal for that role. So every profile
carries a `contested` overlay: the Family 2 integrity verdicts on the very
sessions that support it. Family 2 needs no policy — it is the one family that
works on an unonboarded deployment — which is exactly what makes policy
learnable without a policy. A profile supported by sessions carrying
param_taint or entity_consistency violations is never presented as clean
observed practice.

AN INTENT IS NOT ALWAYS ONE CALL. A business operation is often a SEQUENCE —
fetch the record, pull the report, score it, decide — and mining one tool at a
time reports that as several unrelated operations with no hint they belong
together. `workflows.py` mines the contiguous runs; `profiles.py`'s
`followed_by` is the pairwise shadow of the same signal and is kept because it
is cheap and answers a narrower question (closing obligations).

THE ANSWER KEY IS NOT AN INPUT. A deployment may already stamp an approved
intent name on its spans (`app.intent`). Mining must not read it: a miner that
consumes the label it is meant to predict measures nothing. It is exposed
separately as `observed_intent_labels` for SCORING a mined catalog against a
hand-authored one, and `profile_tools()` never selects it.
"""

from .profiles import (
    ToolProfile,
    tool_profiles,
    caller_invariants,
    following_tools,
    observed_intent_labels,
)
from .cohorts import Cohort, cohort_contrasts
from .episodes import Episode, EpisodeShape, episode_shapes, explained_fraction, session_episodes
from .workflows import Workflow, WorkflowGroup, frequent_workflows, group_workflows

__all__ = [
    "ToolProfile",
    "tool_profiles",
    "caller_invariants",
    "following_tools",
    "observed_intent_labels",
    "Cohort",
    "Episode",
    "EpisodeShape",
    "episode_shapes",
    "explained_fraction",
    "session_episodes",
    "cohort_contrasts",
    "Workflow",
    "WorkflowGroup",
    "frequent_workflows",
    "group_workflows",
]
