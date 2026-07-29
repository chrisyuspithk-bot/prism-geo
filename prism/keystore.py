"""Per-engine API keys and engine selection.

Each answer engine is configured independently: a key, an optional base URL
and model override, and an enabled toggle. Any key set in the UI (settings
table) takes priority over the matching environment variable. "Enabled"
engines are the ones evaluations fan out across — which is what makes
cross-engine visibility meaningful.

The presets are the major answer engines; a `custom` slot covers any other
OpenAI-compatible endpoint (self-hosted vLLM, OpenRouter, a proxy, ...).
"""

import os

from .db import connect

# engine key -> (env var, default base url, default model, help text)
PROVIDERS = {
    "chatgpt": ("OPENAI_API_KEY", "https://api.openai.com", "gpt-4o",
                "OpenAI — the engine behind ChatGPT answers"),
    "claude": ("ANTHROPIC_API_KEY", "https://openrouter.ai/api", "anthropic/claude-sonnet-4",
               "Claude — via OpenRouter, or point base URL at an Anthropic-compatible endpoint"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta",
               "gemini-2.5-flash",
               "Gemini — Google's native API (key in query string, not Bearer header)"),
    "perplexity": ("PERPLEXITY_API_KEY", "https://api.perplexity.ai", "sonar",
                   "Perplexity — Sonar API is OpenAI-compatible"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com", "deepseek-chat",
                 "DeepSeek — cheap OpenAI-compatible chat, good default for trying things out"),
    "custom": ("PRISM_API_KEY", "", "",
               "Custom — any OpenAI-compatible endpoint (set base URL + model)"),
}


def get(name: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (name,)).fetchone()
    return row["value"] if row else ""


def set_value(name: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     (name, value))


def _engine(engine: str) -> dict:
    env, base, model, help_text = PROVIDERS[engine]
    db_key = get(f"{engine}_api_key")
    env_key = os.environ.get(env, "")
    enabled = get(f"{engine}_enabled")
    return {
        "name": engine, "help": help_text,
        "api_key": db_key or env_key,
        "source": "ui" if db_key else ("env" if env_key else ""),
        "base_url": get(f"{engine}_base_url") or base,
        "model": get(f"{engine}_model") or model,
        # default to enabled once a key exists, unless explicitly disabled
        "enabled": (enabled == "1") or (enabled == "" and bool(db_key or env_key)),
    }


def provider_status() -> list[dict]:
    """All engines with config state, for the settings page."""
    return [_engine(e) for e in PROVIDERS]


def active_engines() -> list[dict]:
    """Engines that are enabled AND have a key — what evaluations run against."""
    return [e for e in provider_status() if e["enabled"] and e["api_key"]]


def has_any_key() -> bool:
    return bool(active_engines())


def active_config() -> tuple[str, str, str, str]:
    """(provider, api_key, base_url, model) of the first active engine.

    Kept for single-engine callers (onboarding prompt generation). The
    multi-engine run path uses active_engines() directly.
    """
    engines = active_engines()
    if engines:
        e = engines[0]
        return e["name"], e["api_key"], e["base_url"], e["model"]
    return ("deepseek", "", os.environ.get("PRISM_API_BASE", "https://api.deepseek.com"),
            os.environ.get("PRISM_MODEL", "deepseek-chat"))
