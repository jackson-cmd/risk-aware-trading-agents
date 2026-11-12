"""Any OpenAI-compatible chat-completions endpoint: OpenAI, DeepSeek, Groq, OpenRouter,
Together, Qwen (DashScope compatible mode), or a local Ollama / vLLM / LM Studio server.

Config: ``llm.base_url`` (default https://api.openai.com/v1), ``llm.model``,
``llm.api_key_env`` (the environment variable that holds the key; local servers
usually need none).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from ..config import LLMConfig, env

log = logging.getLogger(__name__)


class OpenAICompatProvider:
    name = "openai-compatible"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        self.key = env(cfg.api_key_env)
        self.session = requests.Session()

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        schema_hint = "Reply with one JSON object that matches this JSON schema:\n" + json.dumps(schema)
        body = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system + "\n\n" + schema_hint},
                         {"role": "user", "content": user}],
            "temperature": self.cfg.temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            r = self.session.post(f"{self.base}/chat/completions", headers=headers, json=body, timeout=self.cfg.timeout)
            if r.status_code == 400 and "response_format" in r.text:
                body.pop("response_format")
                r = self.session.post(f"{self.base}/chat/completions", headers=headers, json=body,
                                      timeout=self.cfg.timeout)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            log.warning("LLM request failed: %s", exc)
            return None
        return parse_json_object(content)


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
