#!/usr/bin/env python3
"""SecureBank — GOVERNED agent (the "after"), as a Prefront MCP CLIENT.

The LLM (OpenAI gpt-4o-mini) has NO database access. It is given the approved
intents that **Prefront exposes over MCP**, maps the request to one, and calls it
over the wire. Prefront — the semantic-mcp-server running as a separate process
per caller identity (ACT_AS injected there, never by the agent) — enforces policy
and returns the decision. This is the real deployment shape: customer LLM ⇄
Prefront MCP server ⇄ database. Nothing governed runs in this process.

One MCP server per caller (identity is per-process in Prefront). URLs are
configured per caller (env GOVERNED_MCP_<CALLER>).
"""

from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.sse import sse_client
from openai import AsyncOpenAI

MODEL = os.environ.get("GOVERNED_MODEL", "gpt-4o-mini")
BASE_URL = os.environ.get("GOVERNED_BASE_URL", "https://api.openai.com/v1")
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("NVIDIA_API_KEY") or ""

# ONE Prefront MCP server. The caller's identity is presented per connection by
# this (trusted) orchestrator via ?act_as= — Prefront resolves it server-side;
# the LLM never sees or sets it. (In production this is an authenticated session.)
MCP_URL = os.environ.get("GOVERNED_MCP_URL", "http://localhost:8090/sse")
CALLER_EMAIL = {
    "maria": "maria.lopez@securebank.example",
    "sam":   "sam.carter@securebank.example",
    "tom":   "tom.reed@securebank.example",
    "priya": "priya.shah@securebank.example",
}

SYSTEM = (
    "You are an assistant for SecureBank running behind the Prefront governed "
    "runtime. You have NO database access and cannot write or run SQL — you may "
    "ONLY act by calling one of the approved-intent tools provided. The runtime "
    "injects the caller's identity and enforces policy; you never choose it. Map "
    "the request to exactly one tool, taking arguments from the text (extract "
    "numeric account/loan ids and amounts; pass customer names as given). If no "
    "approved tool genuinely fits — a prediction, forecast, free-form analysis, or "
    "raw SQL — do NOT call any tool and say there is no approved operation for it."
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
    async with sse_client(url, headers={"X-Prefront-Act-As": act_as}) as (read, write):
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
            return decision


def run_agent(question: str, caller_key: str) -> dict:
    """Connect to the ONE Prefront MCP server as `caller` and return its decision."""
    email = CALLER_EMAIL.get(caller_key)
    if not email:
        return {"outcome": "ERROR", "error": f"unknown caller {caller_key!r}"}
    try:
        return asyncio.run(_run_async(question, MCP_URL, email))
    except Exception as e:  # MCP transport / LLM error — surface it, don't crash the demo
        return {"outcome": "ERROR", "error": f"{type(e).__name__}: {e}",
                "intent": None, "args": {}}
