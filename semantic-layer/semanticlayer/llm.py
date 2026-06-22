"""Multi-provider LLM client for the semantic mapper.

OpenAI-compatible (NVIDIA / DeepSeek / Grok / Groq / OpenAI). Mirrors the Skill
Builder's provider presets so the same API key works across both design-time
tools. Injectable (``client=``) for offline tests.

The semantic mapper is the only agentic step in this program, and it runs ONLY
at design time. Its output is always a *candidate* model (design §2, §23.2).
"""

from __future__ import annotations

import os
from typing import Optional

PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.3-70b-instruct",
        "key_env": "NVIDIA_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3",
        "key_env": "XAI_API_KEY",
        "key_env_alts": ["GROK_API_KEY"],
    },
    "groq": {  # Groq Cloud (gsk_ keys) — hosts open models; NOT xAI Grok.
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
        "key_env_alts": ["XAI_API_KEY"],
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
}
PROVIDER_ALIASES = {"xai": "grok", "x.ai": "grok", "llama": "nvidia"}
DEFAULT_PROVIDER = "groq"


class LLMClient:
    """Thin OpenAI-compatible chat client with JSON-mode handling."""

    def __init__(
        self,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        client=None,
    ) -> None:
        raw = (
            provider or os.environ.get("SEMANTICLAYER_PROVIDER")
            or os.environ.get("SKILLBUILDER_PROVIDER", DEFAULT_PROVIDER)
        ).lower()
        self.provider = PROVIDER_ALIASES.get(raw, raw)
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"unknown provider {raw!r}; choose from "
                f"{list(PROVIDERS) + list(PROVIDER_ALIASES)}"
            )
        preset = PROVIDERS[self.provider]
        self.model = (
            model
            or os.environ.get("SEMANTICLAYER_MODEL")
            or os.environ.get("SKILLBUILDER_MODEL")
            or preset["model"]
        )
        self.temperature = temperature
        self._client = client  # injectable for tests / offline runs
        self._base_url = (
            base_url
            or os.environ.get("SEMANTICLAYER_BASE_URL")
            or os.environ.get("SKILLBUILDER_BASE_URL")
            or preset["base_url"]
        )
        self._key_envs = [preset["key_env"], *preset.get("key_env_alts", [])]
        self._api_key = api_key or next(
            (
                os.environ[e]
                for e in (*self._key_envs, "OPENAI_API_KEY")
                if os.environ.get(e)
            ),
            None,
        )

    @property
    def supports_json_mode(self) -> bool:
        """deepseek-reasoner (R1) rejects response_format/temperature; chat is fine."""
        return "reasoner" not in self.model.lower()

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            if not self._api_key:
                raise RuntimeError(
                    f"No API key for provider '{self.provider}'. "
                    f"Set {' or '.join(self._key_envs)} (or pass api_key=)."
                )
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    def complete(self, system: str, user: str) -> str:
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.supports_json_mode:
            kwargs["temperature"] = self.temperature
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
