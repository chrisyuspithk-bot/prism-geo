"""Run prompts against answer engines and persist parsed results.

Each engine may speak a different API dialect. Gemini uses Google's native
/generateContent endpoint (key in query string, different JSON shape); every
other engine is treated as OpenAI-compatible chat/completions.
"""

import json as _json
import os
from datetime import datetime, timezone

import httpx

from .db import connect, q1
from .extract import find_citations, find_mentions
from .workspace import alias_map, brand_domains

SYSTEM_PROMPT = (
    "You are an answer engine. Answer the user's question helpfully and "
    "concretely, naming specific brands where relevant. When you rely on "
    "sources, include their URLs inline in your answer as plain links."
)


def active_engine() -> tuple[str, str, str, str]:
    """(provider, api_key, base_url, model) resolved from settings + env."""
    from . import keystore
    return keystore.active_config()


async def _query_gemini(prompt: str, key: str, base: str, model: str) -> tuple[str, str]:
    """Query Gemini native API (generateContent)."""
    url = f"{base.rstrip('/')}/models/{model}:generateContent?key={key}"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
            return "ok", data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:
        return "error", str(exc)


async def _query_openai(prompt: str, key: str, base: str, model: str) -> tuple[str, str]:
    """Query any OpenAI-compatible chat/completions endpoint."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return "ok", data["choices"][0]["message"]["content"]
    except Exception as exc:
        return "error", str(exc)


async def query_engine(prompt: str, *, engine: dict | None = None) -> tuple[str, str]:
    """Query one answer engine. Returns (status, response_text).

    `engine` is a keystore engine dict; falls back to the first active one.
    """
    if engine is None:
        _, key, base, mdl = active_engine()
        name = ""
    else:
        key, base, mdl, name = engine["api_key"], engine["base_url"], engine["model"], engine["name"]
    if not key:
        return "error", "No API key configured — add one at /settings/keys."

    if name == "gemini":
        return await _query_gemini(prompt, key, base, mdl)
    return await _query_openai(prompt, key, base, mdl)


def store_run(conn, prompt_id: int, model_id: int, status: str, text: str,
              error: str = "", aliases: dict | None = None,
              brand_domains: set[str] | None = None, job_id: int | None = None,
              tenant_id: int = 1) -> int:
    """Persist one run and its extracted mentions/citations, scoped to a tenant.

    Mention extraction only recognizes the tenant's own brand + competitors, and
    any new brand discovered in the answer is created within the same tenant, so
    one client's answer text never pollutes another's brand list.
    """
    ran_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO runs (prompt_id, model_id, ran_at, response_text, status, error, job_id, tenant_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (prompt_id, model_id, ran_at, text, status, error, job_id, tenant_id),
    )
    run_id = cur.lastrowid
    if status == "ok":
        brand_ids = {
            r["name"]: r["id"] for r in conn.execute(
                "SELECT id, name FROM brands WHERE tenant_id = ?", (tenant_id,))
        }
        for m in find_mentions(text, aliases):
            bid = brand_ids.get(m["brand"])
            if bid is None:
                cur = conn.execute(
                    "INSERT INTO brands (name, slug, tenant_id) VALUES (?, ?, ?)",
                    (m["brand"], m["brand"].lower().replace(" ", "-"), tenant_id),
                )
                bid = cur.lastrowid
                brand_ids[m["brand"]] = bid
            conn.execute(
                "INSERT OR REPLACE INTO mentions (run_id, brand_id, position, count)"
                " VALUES (?, ?, ?, ?)",
                (run_id, bid, m["position"], m["count"]),
            )
        for c in find_citations(text, brand_domains):
            conn.execute(
                "INSERT OR IGNORE INTO citations (run_id, url, domain, category)"
                " VALUES (?, ?, ?, ?)",
                (run_id, c["url"], c["domain"], c["category"]),
            )
    return run_id


def _model_id(conn, name: str) -> int:
    model = q1(conn, "SELECT id FROM models WHERE name = ?", (name,))
    if model is not None:
        return model["id"]
    cur = conn.execute("INSERT INTO models (name, kind) VALUES (?, 'api')", (name,))
    return cur.lastrowid


async def run_prompt(prompt_id: int, engine: dict | None = None,
                     job_id: int | None = None) -> int:
    """Full pipeline for one (prompt, engine): query, extract, store.

    The DB reads happen in one short connection, the network call runs with no
    connection held, and the write is a second short connection. Holding a
    connection across `await query_engine` would keep a write transaction open
    for the whole request and deadlock the other concurrent run_prompt tasks —
    that was the worker hang. Recorded under the engine's display name so
    per-model filters stay consistent across runs.
    """
    if engine is None:
        from . import keystore
        engines = keystore.active_engines()
        engine = engines[0] if engines else None
    if engine is None:
        raise RuntimeError("No answer engine configured — add a key at /settings/keys.")

    display_name = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini",
                    "perplexity": "Perplexity", "deepseek": "DeepSeek"}.get(
        engine["name"], engine["name"].capitalize())
    with connect() as conn:  # read-only phase: connection released before network
        row = q1(conn, "SELECT text, tenant_id FROM prompts WHERE id = ?", (prompt_id,))
        prompt, tenant_id = row["text"], row["tenant_id"]
        aliases = alias_map(conn, tenant_id)
        domains = brand_domains(conn, tenant_id)

    status, text = await query_engine(prompt, engine=engine)

    with connect() as conn:  # write phase
        model_id = _model_id(conn, display_name)
        return store_run(
            conn, prompt_id, model_id, status, text,
            error="" if status == "ok" else text,
            aliases=aliases, brand_domains=domains, job_id=job_id, tenant_id=tenant_id,
        )
