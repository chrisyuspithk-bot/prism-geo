"""RAG: retrieve relevant chunks and generate grounded copy via LLM."""

import re

import httpx

from . import embeddings
from .db import connect, q
from .extract import gemini_answer_text

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

    return f"""You are a marketing copywriter. Write {format_desc}.

Use the WEBSITE CONTENT below as your primary source for the brand's facts, tone, and voice.

If the USER REQUEST mentions a person, client, competitor, or topic that is NOT covered by the website content (for example a named individual like a client), research that entity — using web search when available — and incorporate accurate, relevant details about them into the copy.

Rules:
- Ground brand facts, features, and claims in the website content.
- For entities from the user request that aren't in the website content, search the web and include what you find.
- Do NOT invent features, pricing, testimonials, or statistics.
- Do NOT include citation markers or source numbers (like [1] or [1, 2]) in your response — write in clean prose.
- Write in the same language as the user's request.

WEBSITE CONTENT:
{context}

USER REQUEST: {query}

YOUR RESPONSE:"""


def generate_with_llm(prompt: str, provider: str = "") -> str:
    """Generate copy with a chosen provider.

    `provider` is an engine name ('gemini', 'deepseek', 'perplexity', 'custom',
    ...). Empty selects Gemini (Google Search grounded) when available, else the
    first enabled engine. Gemini uses its native API with `google_search`;
    every other engine goes through an OpenAI-compatible `/chat/completions`.
    """
    from .keystore import active_engines

    engines = active_engines()
    if not engines:
        return "[Error: No LLM engine configured. Set an API key in Settings → Engine Keys.]"

    if provider:
        engine_info = next((e for e in engines if e["name"] == provider), None)
        if not engine_info:
            return f"[Error: Provider '{provider}' isn't enabled or has no API key.]"
    else:
        engine_info = next((e for e in engines if e["name"] == "gemini"), None) or engines[0]

    name = engine_info["name"]
    key = engine_info["api_key"]
    if not key or not key.strip():
        return f"[Error: {name} API key is empty. Set a valid key in Settings → Engine Keys.]"

    if name == "gemini":
        base = engine_info.get("base_url") or "https://generativelanguage.googleapis.com/v1beta"
        model = engine_info.get("model") or "gemini-2.5-flash"
        try:
            resp = httpx.post(
                f"{base.rstrip('/')}/models/{model}:generateContent?key={key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "tools": [{"google_search": {}}],
                },
                timeout=90,
            )
            if resp.status_code >= 400:
                detail = resp.text[:500]
                try:
                    detail = str(resp.json())
                except Exception:
                    pass
                return f"[Error: Gemini {resp.status_code}: {detail}]"
            data = resp.json()
            if not data.get("candidates"):
                block = data.get("promptFeedback", {}).get("blockReason", "")
                reason = f" (blocked: {block})" if block else ""
                return f"[Error: Gemini returned no candidates{reason}]"
            return _clean_markdown(gemini_answer_text(data))
        except Exception as e:
            return f"[Error: {e}]"

    # OpenAI-compatible engines (Perplexity, DeepSeek, Custom, ChatGPT, Claude, ...)
    base = engine_info.get("base_url") or "https://api.deepseek.com/v1"
    model = engine_info.get("model") or "deepseek-v4-flash"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 1024}
    if name != "perplexity":
        payload["temperature"] = 0.7
    try:
        resp = httpx.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        data = resp.json()
        if "choices" in data:
            return _clean_markdown(data["choices"][0]["message"]["content"])
        err = data.get("error", {})
        if isinstance(err, dict):
            err = err.get("message", str(data))
        return f"[Error: {err}]"
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
    text = re.sub(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", "", text)    # [1] / [1, 3] citations
    text = re.sub(r"[ \t]{2,}", " ", text)               # collapse gaps left by stripped citations
    text = re.sub(r"[ \t]+([：。，、；！？])", r"\1", text)  # no space before CJK punctuation
    text = re.sub(r"`(.+?)`", r"\1", text)               # `code`
    text = re.sub(r"\n{3,}", "\n\n", text)               # collapse excessive newlines
    return text.strip()
