"""Notifications to Telegram, Discord or Slack. Each channel is used only when its env vars are set."""

from __future__ import annotations

import logging

import requests

from .config import NotifyConfig, env

log = logging.getLogger(__name__)


def send(cfg: NotifyConfig, text: str, event: str = "orders") -> list[str]:
    if event not in cfg.events:
        return []
    sent = []
    token, chat = env(cfg.telegram_bot_token_env), env(cfg.telegram_chat_id_env)
    try:
        if token and chat:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[:4000]}, timeout=10)
            sent.append("telegram")
        hook = env(cfg.discord_webhook_env)
        if hook:
            requests.post(hook, json={"content": text[:1900]}, timeout=10)
            sent.append("discord")
        hook = env(cfg.slack_webhook_env)
        if hook:
            requests.post(hook, json={"text": text[:3000]}, timeout=10)
            sent.append("slack")
    except requests.RequestException as exc:
        log.warning("notification failed: %s", exc)
    return sent
