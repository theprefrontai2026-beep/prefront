"""The client must cost nothing but the standard library to import.

This is not a performance concern. The client goes into a customer's agent
process, and the spec's integration promise is "under 50 lines" — a client that
dragged in PyYAML, FastAPI or an HTTP library would be a dependency conflict
waiting to happen in the process least able to absorb one.

It is also a regression test for a real break. `warrantservice/__init__.py`
once imported `config` at module scope, which imports PyYAML; the demo
container ships no PyYAML because it needs none, and it died on startup
importing a client that needs none either. Nothing caught it until Docker did.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Everything the client must NOT drag in. `cryptography` is absent from this
# list on purpose: the engine needs it to verify a signature, so an agent
# process already has it.
FORBIDDEN = ("fastapi", "starlette", "uvicorn", "yaml", "pydantic", "httpx", "requests")

# Importing was never the whole promise — USING the client is. An earlier
# version of this probe only imported, so a lazy `import jwt` inside the DPoP
# helpers passed here and then killed the demo container the moment the client
# asked for a task token. Exercise every client-side path an agent walks.
PROBE = f"""
import sys
sys.path.insert(0, {str(ROOT / "warrant")!r})
sys.path.insert(0, {str(ROOT / "warrant-service")!r})

from warrant import SigningKey
from warrantservice import RemotePolicyDecisionService, ServiceError
from warrantservice.dpop import key_thumbprint, make_proof

client = RemotePolicyDecisionService("http://127.0.0.1:1")
key = SigningKey.generate("probe")

# Proof-of-possession, client side: a thumbprint and a signed proof.
jkt = key_thumbprint(key)
proof = make_proof(key, method="POST", url="https://tools.example/call",
                   access_token="some-token")
assert jkt and proof.count(".") == 2

# And the codec, which every response goes through.
from warrantservice import codec
codec.decision_from_wire({{
    "effect": "deny", "tree_id": "t", "node_id": "n", "mission_id": "m",
    "checks": [], "reasons": [], "step_up_delta": [], "decided_at": 1,
    "policy_version": "v",
}})

leaked = [m for m in {FORBIDDEN!r} if m in sys.modules]
print(",".join(leaked))
"""


def test_importing_the_client_pulls_in_no_heavy_dependency():
    """Run in a FRESH interpreter: the test session already has FastAPI loaded,
    so checking `sys.modules` in-process would prove nothing."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    leaked = [m for m in result.stdout.strip().split(",") if m]
    assert not leaked, (
        f"importing the client loaded {leaked}. The client ships into a "
        "customer's agent process and must need the standard library alone"
    )


def test_the_service_side_still_imports_fine():
    """The lazy surface must not have broken the parts that DO need the deps."""
    from warrantservice import Settings, create_app, from_env  # noqa: F401

    assert callable(create_app)
