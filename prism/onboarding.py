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




_SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; prism-geo/1.0; +https://prism-geo.fly.dev)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
}


async def analyze_website(url: str) -> dict:
    """Fetch homepage, return {url, title, description, headings} (best effort)."""
    if not url:
        return {"url": "", "title": "", "description": "", "headings": []}
    target = url if "://" in url else f"https://{url}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(target, headers=_SCRAPE_HEADERS)
            if resp.status_code != 200:
                return {"url": target, "title": "", "description": "",
                        "headings": [], "error": f"HTTP {resp.status_code}"}
            html = resp.text[:200_000]
    except Exception as exc:
        return {"url": target, "title": "", "description": "", "headings": [],
                "error": str(exc)[:200]}

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
                        f"{api_base.rstrip('/')}/chat/completions",
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


async def discover_competitors(domain: str, brand_name: str = "",
                               lang: str = "en") -> dict:
    """Crawl up to 20 pages of a website, understand the business, and discover
    competitors via an LLM with internet search (Perplexity or Gemini).

    Returns {"business": str, "competitors": [str], "pages_analyzed": int,
             "error": str | None}.
    """
    import asyncio
    from . import crawler

    result = {"business": "", "competitors": [], "pages_analyzed": 0, "error": None}

    # --- Phase 1: Crawl up to 20 pages (in thread to avoid blocking) ----------
    urls = await asyncio.to_thread(crawler.discover, domain)
    urls = urls[:20]

    pages: list[dict] = []
    for url in urls:
        page = await asyncio.to_thread(crawler.fetch_page, url)
        if page is not None:
            pages.append(page)

    result["pages_analyzed"] = len(pages)
    if not pages:
        result["error"] = "Could not fetch any pages from the website"
        return result

    # --- Phase 2: Build business summary from pages ---------------------------
    titles = [p["title"] for p in pages if p["title"]]
    headings: list[str] = []
    for p in pages:
        if p["headings"]:
            headings.extend(p["headings"].split("\n"))
    # Take a sample of page content (first 500 chars from top 5 pages)
    content_samples: list[str] = []
    for p in pages[:5]:
        if p["content"]:
            content_samples.append(p["content"][:500])

    page_summary = ""
    if titles:
        page_summary += f"Page titles: {'; '.join(titles[:10])}\n"
    if headings:
        unique_h = list(dict.fromkeys(h.strip() for h in headings if len(h.strip()) > 10))
        page_summary += f"Key headings: {'; '.join(unique_h[:20])}\n"
    if content_samples:
        page_summary += f"Content samples: {' | '.join(content_samples)}"

    # --- Phase 3: Discover competitors via LLM with search --------------------
    engines = keystore.provider_status()
    # Prefer Perplexity (native search) or Gemini (Google grounding) first
    search_engine = None
    for name in ("perplexity", "gemini"):
        e = next((e for e in engines if e["name"] == name and e["enabled"] and e["api_key"]), None)
        if e:
            search_engine = e
            break
    # Fall back to any available engine
    if search_engine is None:
        search_engine = next((e for e in engines if e["enabled"] and e["api_key"]), None)

    if search_engine is None:
        # No LLM available — try free DuckDuckGo fallback
        competitors = await _ddg_competitor_fallback(brand_name or domain, pages)
        if competitors:
            result["competitors"] = competitors
        else:
            result["error"] = "No LLM key configured and DuckDuckGo fallback found nothing"
        return result

    key = search_engine["api_key"]
    base = search_engine["base_url"]
    model = search_engine["model"]
    engine_name = search_engine["name"]

    if lang == "zh-TW":
        ask = (
            f"你正在分析一個品牌網站。以下是從該網站爬取的 {len(pages)} 個頁面摘要：\n\n"
            f"網域：{domain}\n"
            f"品牌名稱（如果知道）：{brand_name or '請從內容推斷'}\n\n"
            f"{page_summary}\n\n"
            f"請執行以下兩個任務：\n"
            f"1. 用 2-3 句簡短描述呢個品牌嘅業務性質同市場定位（用繁體中文）。\n"
            f"2. 搜尋互聯網，列出佢嘅主要競爭對手（8-15 個品牌名稱），"
            f"只限真實存在嘅品牌。\n\n"
            f"請只回傳以下 JSON 格式，唔好加任何 markdown 或其他文字：\n"
            f'{{"business": "業務描述", "competitors": ["品牌A", "品牌B", ...]}}'
        )
    else:
        ask = (
            f"You are analyzing a brand website. Here's a summary of {len(pages)} "
            f"pages crawled from the site:\n\n"
            f"Domain: {domain}\n"
            f"Brand name (if known): {brand_name or 'please infer from content'}\n\n"
            f"{page_summary}\n\n"
            f"Please do two things:\n"
            f"1. Describe the brand's business nature and market position in 2-3 "
            f"sentences.\n"
            f"2. Search the web and list its main competitors (8-15 real brand names).\n\n"
            f"Return ONLY a JSON object in this format, no markdown or other text:\n"
            f'{{"business": "description here", "competitors": ["Brand A", "Brand B", ...]}}'
        )

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            if engine_name == "gemini":
                url = f"{base.rstrip('/')}/models/{model}:generateContent?key={key}"
                resp = await client.post(url, json={
                    "contents": [{"parts": [{"text": ask}]}],
                    "tools": [{"google_search": {}}],
                })
                resp.raise_for_status()
                data = resp.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                resp = await client.post(
                    f"{base.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "temperature": 0.4,
                          "messages": [{"role": "user", "content": ask}]},
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]

        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            parsed = json.loads(match.group(0))
            result["business"] = str(parsed.get("business", "")).strip()
            comps = parsed.get("competitors", [])
            if isinstance(comps, list):
                cleaned: list[str] = []
                for c in comps:
                    name = str(c).strip()
                    # Skip entries that look like sentences/descriptions, not brand names
                    if len(name) > 60:
                        continue
                    if any(ch in name for ch in ("包括", "還包括", "替代方案", "also ", "includes ", "alternatives")):
                        continue
                    # Strip parenthetical context with CJK characters
                    name = re.sub(r"\s*\([^)]*[\u4e00-\u9fff][^)]*\)", "", name).strip()
                    if name and len(name) > 1:
                        cleaned.append(name)
                result["competitors"] = cleaned
    except Exception as exc:
        result["error"] = f"LLM competitor discovery failed: {str(exc)[:200]}"
        # Try DuckDuckGo fallback
        competitors = await _ddg_competitor_fallback(brand_name or domain, pages)
        if competitors:
            result["competitors"] = competitors

    return result


async def _ddg_competitor_fallback(query: str, pages: list[dict]) -> list[str]:
    """Free DuckDuckGo fallback: search for competitors of this brand."""
    import asyncio
    # Build a search query from page titles/headings
    terms = query
    if not terms:
        for p in pages[:3]:
            if p["title"]:
                parts = re.split(r"[|—–-]", p["title"])
                terms = parts[0].strip()
                break
        if not terms:
            terms = pages[0].get("headings", "").split("\n")[0] if pages else ""
    if not terms:
        return []

    search_q = f'"{terms}" competitors similar brands'

    def _fetch():
        try:
            resp = httpx.get(
                f"https://api.duckduckgo.com/?q={quote(search_q)}&format=json&no_html=1&skip_disambig=1",
                timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    data = await asyncio.to_thread(_fetch)
    if data is None:
        return []

    # Extract potential brand names from RelatedTopics and Abstract
    names: list[str] = []
    if data.get("AbstractText"):
        names.append(data["AbstractText"])
    for topic in data.get("RelatedTopics", []):
        text = topic.get("Text", "")
        if text:
            names.append(text)

    # Simple brand-name extraction: capitalized words, multi-word names
    competitors: set[str] = set()
    brand_re = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b")
    for line in names:
        for m in brand_re.finditer(line):
            name = m.group(1).strip()
            if len(name) > 2 and name.lower() not in ("the", "and", "for", "best", "top", "most"):
                competitors.add(name)

    return sorted(competitors)[:15]

