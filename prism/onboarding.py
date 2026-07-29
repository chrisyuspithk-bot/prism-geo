"""Brand onboarding: turn a website + competitor list into a tracked workspace.

Three jobs:
1. analyze_website — fetch the brand homepage and extract a lightweight
   description (title, meta description, headings).
2. search_context — query DuckDuckGo's free Instant Answer API for market
   context (related topics, snippets) around the brand + competitors.
3. generate_prompts — use website + search context + LLM (or fallback
   templates) to produce buyer-style tracking questions.

All degrade gracefully: no API key? no problem — templates still work.
No network? search_context and website fetch silently fall back.
"""

import json
import re
from urllib.parse import quote

import httpx

from .extract import domain_of  # noqa: F401  (re-export)
from . import keystore

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META_RE = re.compile(
    r'<meta[^>]+name=["\'](?:description|og:description)["\'][^>]*>', re.I)
CONTENT_RE = re.compile(r'content=["\'](.*?)["\']', re.I | re.S)
H_RE = re.compile(r"<h[12][^>]*>(.*?)</h[12]>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")




async def analyze_website(url: str) -> dict:
    """Fetch homepage, return {url, title, description, headings} (best effort)."""
    if not url:
        return {"url": "", "title": "", "description": "", "headings": []}
    target = url if "://" in url else f"https://{url}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(target, headers={"User-Agent": "prism-geo/1.0"})
            html = resp.text[:200_000]
    except Exception:
        return {"url": target, "title": "", "description": "", "headings": []}

    def clean(s: str) -> str:
        return TAG_RE.sub("", s).strip()

    title = clean(TITLE_RE.search(html).group(1)) if TITLE_RE.search(html) else ""
    description = ""
    for m in META_RE.finditer(html):
        c = CONTENT_RE.search(m.group(0))
        if c:
            description = clean(c.group(1))
            break
    headings = [clean(h) for h in H_RE.findall(html)][:6]
    return {"url": target, "title": title, "description": description,
            "headings": [h for h in headings if h]}


GENERIC_PROMPTS = [
    ("Best {market} for beginners", "recommendation"),
    ("What are the best {market} brands in {year}?", "comparison"),
    ("Best budget {market}", "budget"),
    ("Which {market} brands are most trustworthy?", "trust"),
    ("{market} buying guide — what to look for", "guide"),
    ("{market} pros and cons — what users say", "review"),
]

GENERIC_PROMPTS_ZH = [
    ("新手適合的 {market} 推薦", "recommendation"),
    ("{market} 有邊啲選擇？", "alternatives"),
    ("平價 {market} 推薦", "budget"),
    ("邊個 {market} 品牌最信得過？", "trust"),
    ("{market} 選購指南同注意事項", "guide"),
    ("{market} 嘅優缺點分析", "review"),
]


async def search_context(brand: str, competitors: list[str]) -> str:
    """Free DuckDuckGo Instant Answer API — no key needed.

    Searches the brand and up to 2 competitors, collects RelatedTopics
    to ground prompt generation in real market search behaviour.
    """
    queries = [brand]
    if competitors:
        queries.append(competitors[0])
        if len(competitors) > 1:
            queries.append(competitors[1])
    queries = [q.strip() for q in queries if q.strip()]
    if not queries:
        return ""

    lines: list[str] = []
    async with httpx.AsyncClient(timeout=10) as client:
        for q in queries:
            try:
                url = f"https://api.duckduckgo.com/?q={quote(q)}&format=json&no_html=1&skip_disambig=1"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue

            if data.get("AbstractText"):
                lines.append(data["AbstractText"])
            for topic in data.get("RelatedTopics", []):
                text = topic.get("Text", "")
                if text:
                    lines.append(text)

    if not lines:
        return ""

    # Deduplicate and trim
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        key = re.sub(r"\s+", " ", line).strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(line.strip())
    return "\n".join(unique[:20])


async def generate_prompts(brand: str, competitors: list[str],
                           site: dict, n: int = 10,
                           lang: str = "en") -> list[dict]:
    """Generate buyer-style tracking prompts. Falls back to generic templates."""
    comp_list = ", ".join(competitors) if competitors else "its main competitors"

    # Build context from website + free DuckDuckGo search
    context = ""
    if site.get("description"):
        context = f"\nAbout the brand: {site['description']}"
    elif site.get("title"):
        context = f"\nBrand site title: {site['title']}"

    search = await search_context(brand, competitors)
    if search:
        context += f"\n\nReal search results / related topics for this market:\n{search}"

    _, api_key, api_base, model = keystore.active_config()
    if api_key:
        if lang == "zh-TW":
            ask = (
                f"你正在協助設定品牌「{brand}」的 AI 可見度追蹤。"
                f"競爭者：{comp_list}。{context}\n\n"
                f"請根據以上真實市場資訊，列出 {n} 個潛在客戶在 AI 答案引擎上"
                f"研究呢個市場時最可能問嘅簡短問題——請涵蓋推薦、替代方案、"
                f"預算、購買指南、優缺點分析和信任度等不同類型。\n\n"
                f"重要：問題中唔好包含品牌名「{brand}」或任何競爭者名稱——"
                f"呢啲應該係類別層級嘅問題，由潛在客戶研究市場時自然提出，"
                f"而唔係針對特定品牌嘅搜尋。我哋想睇嘅係品牌喺一般市場問題中"
                f"嘅出現頻率。\n\n"
                f"只回傳 JSON 陣列，每個物件包含 text 和 tag 欄位"
                f"（tag 使用簡短英文單字，例如 recommendation, alternatives, "
                f"budget, guide, review, trust）。"
                f"所有問題必須使用繁體中文撰寫。不要使用 markdown。"
            )
        else:
            ask = (
                f"You help set up AI-visibility tracking for the brand '{brand}'. "
                f"Its competitors: {comp_list}.{context}\n\n"
                f"List {n} short questions a potential customer would ask an AI answer "
                f"engine when researching this market — a mix of recommendations, "
                f"alternatives, budget, guides, reviews and trust questions.\n\n"
                f"IMPORTANT: Do NOT include the brand name '{brand}' or any competitor "
                f"names in the questions. These should be category-level questions "
                f"a shopper naturally asks when researching a market — not "
                f"brand-specific searches. We're tracking whether the brand appears "
                f"in answers to general market questions.\n\n"
                f"Return ONLY a JSON array of objects with keys text and tag "
                f"(tag is one short lowercase word like recommendation, alternatives, "
                f"budget, guide, review, trust). No markdown."
            )
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                if "generativelanguage.googleapis.com" in api_base:
                    resp = await client.post(
                        f"{api_base.rstrip('/')}/models/{model}:generateContent?key={api_key}",
                        json={"contents": [{"parts": [{"text": ask}]}]},
                    )
                    resp.raise_for_status()
                    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    resp = await client.post(
                        f"{api_base.rstrip('/')}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": model, "temperature": 0.8,
                              "messages": [{"role": "user", "content": ask}]},
                    )
                    resp.raise_for_status()
                    raw = resp.json()["choices"][0]["message"]["content"]
            match = re.search(r"\[.*\]", raw, re.S)
            items = json.loads(match.group(0)) if match else []
            prompts = [
                {"text": str(i["text"]).strip(), "tag": str(i.get("tag", "")).strip()}
                for i in items if i.get("text")
            ]
            if prompts:
                return prompts[:n]
        except Exception:
            pass  # fall through to templates

    # Fallback: fill generic templates with a guessed market noun.
    market = site.get("title") or brand
    market = re.sub(r"[|—–-].*$", "", market).strip() or "products"
    from datetime import date
    templates = GENERIC_PROMPTS_ZH if lang == "zh-TW" else GENERIC_PROMPTS
    return [
        {"text": t.format(market=market, year=date.today().year), "tag": tag}
        for t, tag in templates
    ]
