"""Optional LLM review layer (off by default).

A bull analyst, a bear analyst and a risk judge read the structured evidence for
one buy candidate and return a bounded conviction adjustment and an optional
veto. The LLM never creates trades, never changes sizes or limits, and news
headlines reach it only as quoted, untrusted data.
"""

from __future__ import annotations

from ..config import LLMConfig
from .reviewer import LLMReviewer, LLMVerdict, ReviewRecord

OLLAMA_URL = "http://localhost:11434/v1"
CHOICES = ("openai", "ollama", "anthropic", "compat")
DEFAULT_MODELS = {"openai": "gpt-5.6-luna", "ollama": "qwen2.5:3b", "anthropic": "claude-opus-5-5"}


def parse_choice(choice: str) -> tuple[str, str]:
    """'openai:gpt-5.6-luna' -> ('openai', 'gpt-5.6-luna'); 'ollama:qwen2.5:3b' -> ('ollama', 'qwen2.5:3b')."""
    kind, _, model = (choice or "").strip().partition(":")
    kind = kind.lower()
    if kind not in CHOICES:
        raise ValueError(f"LLM choice must look like provider:model with provider one of {', '.join(CHOICES)} "
                         f"(or 'off'), got '{choice}'")
    model = model.strip() or DEFAULT_MODELS.get(kind, "")
    if not model:
        raise ValueError(f"name a model, for example {kind}:my-model")
    return kind, model


def llm_config(choice: str, base: LLMConfig | None = None, base_url: str | None = None,
               key_env: str | None = None, effort: str | None = None) -> LLMConfig:
    """An enabled LLMConfig for a short choice such as 'openai:gpt-5.6-luna', 'ollama:qwen2.5:3b',
    'anthropic:claude-opus-5-5' or 'compat:deepseek-chat' (any OpenAI-compatible server; give base_url)."""
    kind, model = parse_choice(choice)
    c = (base or LLMConfig()).model_copy(deep=True)
    c.enabled = True
    c.model = model
    if kind == "anthropic":
        c.provider = "anthropic"
        c.base_url = base_url
        return c
    c.provider = "openai"
    if kind == "ollama":
        c.base_url = base_url or OLLAMA_URL
        c.api_key_env = key_env or "OLLAMA_API_KEY"  # usually unset: a local server needs no key
        c.timeout = max(c.timeout, 180.0)
    elif kind == "compat":
        if not base_url:
            raise ValueError("compat:<model> needs base_url, for example https://api.deepseek.com/v1")
        c.base_url = base_url
        c.api_key_env = key_env or c.api_key_env
    else:
        c.base_url = base_url
        c.api_key_env = key_env or "OPENAI_API_KEY"
    c.reasoning_effort = effort if effort is not None else ("none" if model.startswith(("gpt-5", "gpt-6")) else None)
    return c


def make_provider(cfg: LLMConfig):
    if not cfg.enabled:
        return None
    if cfg.provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    from .openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(cfg)


def make_reviewer(cfg: LLMConfig) -> LLMReviewer | None:
    provider = make_provider(cfg)
    return LLMReviewer(provider, cfg) if provider is not None else None


__all__ = ["LLMReviewer", "LLMVerdict", "ReviewRecord", "llm_config", "make_provider", "make_reviewer",
           "parse_choice"]
