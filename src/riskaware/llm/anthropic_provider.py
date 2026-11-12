"""Claude through the official Anthropic Python SDK (``pip install "risk-aware-trading-agents[llm]"``).

Credentials come from the environment the way the SDK resolves them
(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an ``ant auth login`` profile).
The reply is constrained to a JSON schema with structured outputs. On models
that support it, the server-side refusal fallback is enabled so a safety
decline is retried on a fallback model inside the same call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import LLMConfig

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: LLMConfig):
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError('the anthropic provider needs: pip install "risk-aware-trading-agents[llm]"') from exc
        self.anthropic = anthropic
        self.cfg = cfg
        self.model = cfg.model if cfg.model.startswith("claude") else DEFAULT_MODEL
        kwargs: dict[str, Any] = {"timeout": cfg.timeout, "max_retries": 2}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self.client = anthropic.Anthropic(**kwargs)

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        a = self.anthropic
        request: dict[str, Any] = dict(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            if self.cfg.refusal_fallback and self.model in FALLBACK_MODELS:
                response = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
            else:
                response = self.client.messages.create(**request)
        except a.RateLimitError:
            log.warning("Claude rate limit reached; skipping the LLM review")
            return None
        except a.APIStatusError as exc:
            log.warning("Claude API error %s: %s", exc.status_code, exc.message)
            return None
        except a.APIConnectionError as exc:
            log.warning("cannot reach the Claude API: %s", exc)
            return None
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            log.info("Claude declined the review (%s)", getattr(details, "category", None))
            return None
        if response.stop_reason == "max_tokens":
            log.info("Claude review hit max_tokens; ignored")
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
