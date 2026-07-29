"""RAG: retrieve relevant chunks and generate grounded copy via LLM."""

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
    from .keystore import active_engines, get_key

    engines = active_engines()
    if not engines:
        return "[Error: No LLM engine configured. Set an API key in Settings → Engine Keys.]"

    engine_name = engines[0]
    key = get_key(engine_name)

    # Map engine name to OpenAI-compatible endpoint
    import httpx
    base_urls = {
        "deepseek": "https://api.deepseek.com/v1",
        "chatgpt": "https://api.openai.com/v1",
        "claude": "https://api.anthropic.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "perplexity": "https://api.perplexity.com",
    }
    models = {
        "deepseek": "deepseek-chat",
        "chatgpt": "gpt-4o-mini",
        "claude": "claude-3-haiku-20240307",
        "gemini": "gemini-2.0-flash",
        "perplexity": "llama-3.1-sonar-small-128k-online",
    }
    base = base_urls.get(engine_name, base_urls["deepseek"])
    model = models.get(engine_name, models["deepseek"])

    if engine_name == "gemini":
        try:
            resp = httpx.post(
                f"{base}/models/{model}:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60,
            )
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
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
            return data["choices"][0]["message"]["content"]
        return f"[Error: {data.get('error', {}).get('message', str(data))}]"
    except Exception as e:
        return f"[Error generating copy: {e}]"
