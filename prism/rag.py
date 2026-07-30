"""RAG: retrieve relevant chunks and generate grounded copy via LLM."""

import re

import httpx

from . import embeddings
from .db import connect, q

_RETRIEVAL_K = 5

FORMATS = {
    "linkedin_post": "a LinkedIn post (~150-200 words)",
    "email": "a marketing email (~200-300 words)",
    "ad_copy": "short ad copy (~60-100 words)",
    "blog_intro": "a blog introduction (~150-200 words)",
}

FORMAT_LABELS = {
    "linkedin_post": "LinkedIn Post",
    "email": "Email",
    "ad_copy": "Ad Copy",
    "blog_intro": "Blog Intro",
}


def retrieve(conn, site_id: int, query: str, k: int = _RETRIEVAL_K) -> list[dict]:
    """Return top-k relevant chunks for a query using cosine similarity."""
    rows = q(
        conn,
        """SELECT c.id, c.content, c.embedding, p.url, p.title
           FROM chunks c JOIN pages p ON p.id = c.page_id
           WHERE c.site_id = ? AND c.embedding != ''""",
        (site_id,),
    )
    if not rows:
        return []

    q_vec = embeddings.embed_one(query)
    scored = []
    for r in rows:
        try:
            vec = embeddings.unpack(r["embedding"])
            sim = embeddings.cosine(q_vec, vec)
            scored.append((sim, dict(r)))
        except Exception:
            continue

    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:k]]


def build_prompt(chunks: list[dict], query: str, fmt: str) -> str:
    """Build a grounded generation prompt from retrieved chunks."""
    ctx_parts = []
    for i, c in enumerate(chunks):
        src = f"{c['title'] or c['url']} ({c['url']})"
        ctx_parts.append(f"[{i+1}] Source: {src}\n{c['content']}")

    context = "\n\n---\n\n".join(ctx_parts)
    format_desc = FORMATS.get(fmt, FORMATS["linkedin_post"])

    return f"""You are a marketing copywriter. Using ONLY the website content below, write {format_desc}.

Rules:
- Only use facts, features, and claims that appear in the provided content.
- Keep the brand voice consistent with the source material.
- Do NOT invent features, pricing, testimonials, or statistics not found below.
- Write in the same language as the user's request.

WEBSITE CONTENT:
{context}

USER REQUEST: {query}

YOUR RESPONSE:"""


def generate_with_llm(prompt: str) -> str:
    """Call the configured LLM to generate copy using the first available engine."""
    from .keystore import active_engines

    engines = active_engines()
    if not engines:
        return "[Error: No LLM engine configured. Set an API key in Settings → Engine Keys.]"

    engine_info = engines[0]
    engine_name = engine_info["name"]
    key = engine_info["api_key"]
    if not key or not key.strip():
        return "[Error: API key is empty. Set a valid key in Settings → Engine Keys.]"
    base = engine_info.get("base_url") or "https://api.deepseek.com/v1"
    model = engine_info.get("model") or "deepseek-chat"

    if engine_name == "gemini":
        try:
            resp = httpx.post(
                f"{base}/models/{model}:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60,
            )
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return _clean_markdown(text)
        except Exception as e:
            return f"[Error: {e}]"

    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.7, "max_tokens": 1024},
            timeout=60,
        )
        data = resp.json()
        if "choices" in data:
            text = data["choices"][0]["message"]["content"]
            return _clean_markdown(text)
        return f"[Error: {data.get('error', {}).get('message', str(data))}]"
    except Exception as e:
        return f"[Error generating copy: {e}]"


def _clean_markdown(text: str) -> str:
    """Strip common markdown formatting for clean, human-readable output."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)       # **bold**
    text = re.sub(r"\*(.+?)\*", r"\1", text)            # *italic*
    text = re.sub(r"__(.+?)__", r"\1", text)             # __bold__
    text = re.sub(r"_(.+?)_", r"\1", text)               # _italic_
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)  # ### headers
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)   # - bullet
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)    # 1. numbered
    text = re.sub(r"`(.+?)`", r"\1", text)               # `code`
    text = re.sub(r"\n{3,}", "\n\n", text)               # collapse excessive newlines
    return text.strip()
