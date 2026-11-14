"""Any OpenAI-compatible chat-completions endpoint: OpenAI, DeepSeek, Groq, OpenRouter,
Together, Qwen (DashScope compatible mode), or a local Ollama / vLLM / LM Studio server.

Config: ``llm.base_url`` (default https://api.openai.com/v1), ``llm.model``,
``llm.api_key_env`` (the environment variable that holds the key; local servers
usually need none) and, for reasoning models such as gpt-5.x, ``llm.reasoning_effort``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from ..config import LLMConfig, env

log = logging.getLogger(__name__)

REASONING_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")


def is_reasoning_model(model: str) -> bool:
    return model.lower().split("/")[-1].startswith(REASONING_PREFIXES)


class OpenAICompatProvider:
    name = "openai-compatible"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.model = cfg.model
        self.base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        self.key = env(cfg.api_key_env)
        self.session = requests.Session()
        self.last_raw: str | None = None
        self.last_error: str | None = None
        self.last_usage: dict[str, Any] = {}

    def request_body(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        schema_hint = "Reply with one JSON object that matches this JSON schema:\n" + json.dumps(schema)
        body: dict[str, Any] = {
            "model": self.cfg.model,
            # one JSON response: some OpenAI-compatible gateways answer with server-sent events unless told not to,
            # and the reply could not be parsed
            "stream": False,
            "messages": [{"role": "system", "content": system + "\n\n" + schema_hint},
                         {"role": "user", "content": user}],
            # strict structured output: the reply must match the schema and ends with the object (without it, small
            # and non-reasoning models sometimes keep writing after the JSON until they hit the token cap)
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "review", "schema": schema, "strict": True}},
        }
        effort = self.cfg.reasoning_effort
        if is_reasoning_model(self.cfg.model):
            # reasoning models take max_completion_tokens and fix the temperature unless reasoning is off
            body["max_completion_tokens"] = self.cfg.max_tokens
            if effort:
                body["reasoning_effort"] = effort
            if effort == "none":
                body["temperature"] = self.cfg.temperature
        else:
            body["temperature"] = self.cfg.temperature
            body["max_tokens"] = self.cfg.max_tokens
        return body

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        self.last_raw, self.last_error, self.last_usage = None, None, {}
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = self.request_body(system, user, schema)
        url = f"{self.base}/chat/completions"
        try:
            timeouts = 0
            for _ in range(5):
                try:
                    r = self.session.post(url, headers=headers, json=body, timeout=self.cfg.timeout)
                except requests.Timeout:
                    timeouts += 1
                    if timeouts > 1:
                        raise
                    log.info("LLM request timed out; retrying once")
                    continue
                if r.status_code == 400:  # relax a parameter this endpoint or model does not take, then retry
                    fmt = body.get("response_format", {}).get("type")
                    if fmt == "json_schema" and ("json_schema" in r.text or "response_format" in r.text):
                        log.info("endpoint has no strict JSON schema; asking for a JSON object instead")
                        body["response_format"] = {"type": "json_object"}
                        continue
                    dropped = next((k for k in ("response_format", "temperature", "reasoning_effort",
                                                "max_completion_tokens", "max_tokens")
                                    if k in body and k in r.text), None)
                    if dropped:
                        log.info("endpoint rejected %s; retrying without it", dropped)
                        body.pop(dropped)
                        continue
                if r.status_code == 429:
                    time.sleep(2)
                break
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"] or ""
            self.last_usage = data.get("usage") or {}
        except requests.HTTPError as exc:
            self.last_error = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            log.warning("LLM request failed: %s", self.last_error)
            return None
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("LLM request failed: %s", exc)
            return None
        self.last_raw = content
        parsed = parse_json_object(content)
        if parsed is None:
            self.last_error = "the reply was not a JSON object"
        return parsed


def parse_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply, ignoring code fences and anything the model wrote after it."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        end = text.rfind("}")
        if end <= start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None
