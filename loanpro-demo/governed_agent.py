#!/usr/bin/env python3
"""LoanPro — GOVERNED agent (the "after"), as a Prefront MCP CLIENT.

The LLM (OpenAI gpt-4o-mini) has NO database access. It is given the approved
intents that **Prefront exposes over MCP**, maps the request to one, and calls it
over the wire. Prefront — the semantic-mcp-server running as a separate process
per caller identity (ACT_AS injected there, never by the agent) — enforces policy
and returns the decision. This is the real deployment shape: customer LLM ⇄
Prefront MCP server ⇄ database. Nothing governed runs in this process.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time

from mcp import ClientSession
# Imported as a MODULE, not `from ... import sse_client`: tracing instruments MCP
# by patching `mcp.client.sse.sse_client`, and a name bound at import time would
# keep pointing at the unpatched original — the governed call would then start a
# NEW trace instead of continuing the agent's.
from mcp.client import sse as mcp_sse
from openai import AsyncOpenAI

import prefront_tracing as tracing

_tracer = tracing.get_tracer("loanpro.governed")

MODEL = os.environ.get("GOVERNED_MODEL", "gpt-4o-mini")
BASE_URL = os.environ.get("GOVERNED_BASE_URL", "https://api.openai.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("NVIDIA_API_KEY") or ""

# ONE Prefront MCP server. The caller's identity is presented per connection by
# this (trusted) orchestrator via the X-Prefront-Act-As header — Prefront resolves
# it server-side; the LLM never sees or sets it.
MCP_URL = os.environ.get("GOVERNED_MCP_URL", "http://localhost:8090/sse")
CALLER_EMAIL = {
    "aisha":  "aisha.khan@loanpro.example",
    "ben":    "ben.torres@loanpro.example",
    "olivia": "olivia.reed@loanpro.example",
    "uma":    "uma.patel@loanpro.example",
    "martin": "martin.cole@loanpro.example",
}

SYSTEM = (
    "You are an assistant for LoanPro, a retail loan-origination shop, running "
    "behind the Prefront governed runtime. You have NO database access and cannot "
    "write or run SQL — you may ONLY act by calling one of the approved-intent "
    "tools provided. The runtime injects the caller's identity and enforces policy; "
    "you never choose it. Map the request to exactly one tool, taking arguments "
    "from the text (extract numeric loan/applicant ids and amounts; pass applicant "
    "names as given; for approve/reject requests pass decision='approved' or "
    "'rejected'). If no approved tool genuinely fits — a prediction, forecast, "
    "free-form analysis, or raw SQL — do NOT call any tool and say there is no "
    "approved operation for it."
)

_client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)


def _openai_tools(listing) -> list[dict]:
    """MCP tool listing -> OpenAI tool specs (caller_* params are runtime-injected)."""
    specs = []
    for t in listing.tools:
        schema = t.inputSchema or {"type": "object", "properties": {}}
        props = {k: v for k, v in schema.get("properties", {}).items()
                 if not k.startswith("caller_")}
        required = [r for r in schema.get("required", []) if not r.startswith("caller_")]
        specs.append({"type": "function", "function": {
            "name": t.name,
            "description": (t.description or t.name) + " (approved Prefront intent)",
            "parameters": {"type": "object", "properties": props,
                           "required": required, "additionalProperties": False},
        }})
    return specs


def _outcome(r: dict) -> str:
    status = r.get("status")
    if status == "blocked":
        return "BLOCK (policy)"
    if status == "approval_required":
        return "APPROVAL (policy)"
    if status == "allowed":
        if r.get("masked_fields"):
            return "ALLOW (fields masked)"
        if r.get("row_count") == 0:
            return "ALLOW (0 rows — out of scope)"
        return "ALLOW (scoped to caller)"
    return status or "UNKNOWN"


async def _run_async(question: str, url: str, act_as: str) -> dict:
    # Identity travels as a header (set by this trusted orchestrator), so the SSE
    # URL stays clean and Prefront resolves the caller server-side per connection.
    async with mcp_sse.sse_client(url, headers={"X-Prefront-Act-As": act_as}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = _openai_tools(await session.list_tools())
            resp = await _client.chat.completions.create(
                model=MODEL, tools=tools, tool_choice="auto", temperature=0, max_tokens=400,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": question}])
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return {"intent": None, "args": {}, "status": "blocked",
                        "outcome": "BLOCK (no approved intent)",
                        "reasons": ["no_approved_intent: the request maps to no governed operation"],
                        "answer": msg.content}
            tc = msg.tool_calls[0]
            intent = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await session.call_tool(intent, args)        # ← Prefront enforces here
            text = result.content[0].text if result.content else "{}"
            try:
                decision = json.loads(text)
            except json.JSONDecodeError:
                decision = {"status": "error", "reasons": [text]}
            decision["intent"] = intent
            decision["args"] = args
            decision["outcome"] = _outcome(decision)

            # Second LLM pass: synthesize the governed result into a natural language answer.
            if decision.get("status") == "allowed":
                synth = await _client.chat.completions.create(
                    model=MODEL, temperature=0, max_tokens=200,
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": None, "tool_calls": [
                            {"id": tc.id, "type": "function",
                             "function": {"name": intent, "arguments": tc.function.arguments}}
                        ]},
                        {"role": "tool", "tool_call_id": tc.id, "content": text},
                    ])
                decision["answer"] = synth.choices[0].message.content

            return decision


def run_agent(question: str, caller_key: str) -> dict:
    """Connect to the ONE Prefront MCP server as `caller` and return its decision.

    The MCP SSE transport can flake under concurrent connections (the UI's
    "Run all" opens one per scenario at once), surfacing as an ExceptionGroup /
    JSONDecodeError from a server-side ``TypeError: 'NoneType' object is not
    callable``. That is a transport issue, not a governance decision — a governed
    block/mask/approval returns a dict, it never raises — so a failed attempt is
    always safe to retry. Reads and prechecks are read-only and idempotent, so
    re-running is harmless. Retry with a jittered backoff to stagger the
    concurrent reconnections."""
    email = CALLER_EMAIL.get(caller_key)
    if not email:
        return {"outcome": "ERROR", "error": f"unknown caller {caller_key!r}"}
    attempts = max(1, int(os.environ.get("GOVERNED_MCP_RETRIES", "4")))
    last_err: Exception | None = None
    # One span per governed run, wrapping the retries. The MCP instrumentation
    # carries this context to the Prefront server, so its `govern <intent>` span
    # lands under this one — LLM tool choice and policy decision in one trace.
    with _tracer.start_as_current_span("governed agent") as span:
        tracing.set_attributes(span, {
            tracing.SPAN_KIND: "AGENT",
            tracing.INPUT_VALUE: question,
            "prefront.caller.key": caller_key,
            "prefront.mcp_url": MCP_URL,
        })
        for attempt in range(attempts):
            try:
                decision = asyncio.run(_run_async(question, MCP_URL, email))
            except Exception as e:  # transient MCP transport / LLM error — retry, don't surface yet
                last_err = e
                if attempt < attempts - 1:
                    time.sleep(0.3 * (attempt + 1) + random.uniform(0, 0.4))
                continue
            tracing.set_attributes(span, {
                "agent.attempts": attempt + 1,
                "prefront.intent": decision.get("intent"),
                "prefront.decision": decision.get("status"),
                "prefront.outcome": decision.get("outcome"),
                "prefront.masked_fields": decision.get("masked_fields"),
                "prefront.row_count": decision.get("row_count"),
                tracing.OUTPUT_VALUE: decision.get("answer"),
            })
            return decision
        tracing.set_attributes(span, {"agent.attempts": attempts})
        tracing.mark_error(span, f"{type(last_err).__name__}: {last_err}")
        return {"outcome": "ERROR",
                "error": f"{type(last_err).__name__}: {last_err} (after {attempts} attempts)",
                "intent": None, "args": {}}
