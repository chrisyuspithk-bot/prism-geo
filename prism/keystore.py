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
    "chatgpt": ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o",
                "OpenAI — the engine behind ChatGPT answers"),
    "claude": ("ANTHROPIC_API_KEY", "https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4",
               "Claude — via OpenRouter, or point base URL at an Anthropic-compatible endpoint"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta",
               "gemini-2.5-flash",
               "Gemini — Google's native API (key in query string, not Bearer header)"),
    "perplexity": ("PERPLEXITY_API_KEY", "https://api.perplexity.ai", "sonar",
                   "Perplexity — Sonar API is OpenAI-compatible (no /v1 prefix)"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat",
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
    engines = [_engine(e) for e in PROVIDERS]
    # Dynamic custom engines
    count_str = get("custom_engine_count") or "0"
    try:
        count = int(count_str)
    except ValueError:
        count = 0
    for i in range(1, count + 1):
        key = get(f"custom_{i}_api_key")
        if key:  # only show engines that have at least a key
            engines.append({
                "name": f"custom_{i}",
                "help": get(f"custom_{i}_label") or f"Custom #{i}",
                "api_key": key,
                "source": "ui",
                "base_url": get(f"custom_{i}_base_url") or "",
                "model": get(f"custom_{i}_model") or "",
                "enabled": get(f"custom_{i}_enabled") != "0",
                "custom_id": i,
            })
    return engines


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


def add_custom_engine(label: str = "") -> int:
    """Add a new custom engine slot, return its ID."""
    count_str = get("custom_engine_count") or "0"
    try:
        count = int(count_str)
    except ValueError:
        count = 0
    new_id = count + 1
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     ("custom_engine_count", str(new_id)))
        if label:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         (f"custom_{new_id}_label", label))
    return new_id


def remove_custom_engine(custom_id: int) -> None:
    """Remove a custom engine and all its settings."""
    keys = [f"custom_{custom_id}_api_key", f"custom_{custom_id}_base_url",
            f"custom_{custom_id}_model", f"custom_{custom_id}_enabled",
            f"custom_{custom_id}_label"]
    with connect() as conn:
        for k in keys:
            conn.execute("DELETE FROM settings WHERE key = ?", (k,))
