"""Brand onboarding: turn a website + competitor list into a tracked workspace.

Two jobs, mirroring how real GEO tools set up a brand:
1. analyze_website — fetch the brand homepage and extract a lightweight
   description (title, meta description, headings) used to ground prompt gen.
2. generate_prompts — ask the configured LLM for buyer-style questions a
   customer would ask an answer engine in this market, tagged by intent.

Both degrade gracefully: if the site or the LLM is unreachable we fall back to
generic market prompt templates so setup never dead-ends.
"""

import json
import re

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
    ("What are the best {market} brands in {year}?", "comparison"),
    ("Best {market} for beginners", "recommendation"),
    ("{brand} vs {competitor} — which is better?", "vs"),
    ("Alternatives to {brand} for {market}", "alternatives"),
    ("Best {market} under $100", "budget"),
    ("Which {market} brands are most reliable?", "trust"),
]

GENERIC_PROMPTS_ZH = [
    ("新手適合的 {market} 推薦", "recommendation"),
    ("{brand} 的替代選擇有哪些？", "alternatives"),
    ("平價 {market} 推薦", "budget"),
    ("哪些 {market} 品牌最值得信賴？", "trust"),
    ("{market} 選購指南與注意事項", "guide"),
    ("{brand} 的優缺點分析", "review"),
]


async def generate_prompts(brand: str, competitors: list[str],
                           site: dict, n: int = 10,
                           lang: str = "en") -> list[dict]:
    """Generate buyer-style tracking prompts. Falls back to generic templates."""
    comp_list = ", ".join(competitors) if competitors else "its main competitors"
    context = ""
    if site.get("description"):
        context = f"\nAbout the brand: {site['description']}"
    elif site.get("title"):
        context = f"\nBrand site title: {site['title']}"

    _, api_key, api_base, model = keystore.active_config()
    if api_key:
        if lang == "zh-TW":
            ask = (
                f"你正在協助設定品牌「{brand}」的 AI 可見度追蹤。"
                f"競爭者：{comp_list}。{context}\n\n"
                f"請列出 {n} 個潛在客戶在 AI 答案引擎上研究這個市場時可能會問的"
                f"簡短問題——請涵蓋推薦、替代方案、預算、購買指南、優缺點分析"
                f"和信任度等不同類型。"
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
                f"engine when researching this market — a mix of comparisons, "
                f"'best X for Y', alternatives, budget and trust questions. "
                f"Return ONLY a JSON array of objects with keys text and tag "
                f"(tag is one short lowercase word like comparison, budget, vs, "
                f"alternatives, trust, recommendation). No markdown."
            )
        try:
            async with httpx.AsyncClient(timeout=60) as client:
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
    first_comp = competitors[0] if competitors else "competitors"
    from datetime import date
    templates = GENERIC_PROMPTS_ZH if lang == "zh-TW" else GENERIC_PROMPTS
    return [
        {"text": t.format(market=market, brand=brand, competitor=first_comp,
                          year=date.today().year),
         "tag": tag}
        for t, tag in templates
    ]
