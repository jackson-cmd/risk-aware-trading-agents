"""Optional LLM review layer (off by default).

A bull analyst, a bear analyst and a risk judge read the structured evidence for
one buy candidate and return a bounded conviction adjustment and an optional
veto. The LLM never creates trades, never changes sizes or limits, and news
headlines reach it only as quoted, untrusted data.
"""

from __future__ import annotations

from ..config import LLMConfig
from .reviewer import LLMReviewer, LLMVerdict


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


__all__ = ["LLMReviewer", "LLMVerdict", "make_provider", "make_reviewer"]
