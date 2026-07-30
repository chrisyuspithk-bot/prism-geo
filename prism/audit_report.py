"""Comprehensive GEO audit report — combines crawled website content analysis
with visibility tracking data and LLM-generated insights. Produces PDF-ready HTML.
"""

import json
import re
from datetime import datetime, timezone

import httpx

from . import keystore, queries
from .db import connect

AUDIT_SYSTEM = """\
You are a GEO (Generative Engine Optimization) auditor. You analyze a brand's
website content against AI visibility data to produce a professional audit
report. You are direct, data-specific, and never use generic praise.

For each section, reference specific page URLs, content facts, competitor names,
and visibility metrics from the provided data. Write in the same language as
the brand content (Chinese for Chinese brands, English otherwise).

Return your analysis as a JSON object with these keys:
- "executive_summary": 2-3 paragraph overall assessment
- "content_audit": {
    "page_inventory": [{ "url": "...", "title": "...", "category": "product|about|blog|service|landing|other", "quality": "good|medium|thin", "note": "1-line assessment" }],
    "fact_density": { "pct": 0-100, "assessment": "1 paragraph about verifiable facts vs fluff" },
    "missing_content": [{ "type": "FAQ|case_studies|comparison_guides|testimonials|pricing|tech_specs|white_papers|schema", "severity": "high|medium|low", "note": "why it matters" }],
    "brand_consistency": { "ok": true/false, "issues": ["list of inconsistencies found"] },
    "freshness": { "ok": true/false, "issues": ["outdated copyright", "stale blog", "no dates", etc.] }
  },
- "scoring": {
    "intent_match": { "score": 0-100, "note": "1 sentence" },
    "citeability": { "score": 0-100, "note": "1 sentence" },
    "authority": { "score": 0-100, "note": "1 sentence" },
    "technical": { "score": 0-100, "note": "1 sentence" },
    "trust": { "score": 0-100, "note": "1 sentence" }
  },
- "competitor_insights": "1-2 paragraph analysis of competitor positioning vs the brand",
- "recommendations": [{ "priority": "immediate|short_term|long_term", "action": "specific 1-sentence action item", "effort": "low|medium|high", "impact": "high|medium|low" }]
}"""

# Audit report template strings — keyed by language
_T = {
    "zh-TW": {
        "report_title": "GEO (Generative Engine Optimization) 網站審計報告",
        "audit_date": "審計日期",
        "audit_scope": "審計範圍",
        "scope_desc": "全站爬取 + AI 可見度追蹤數據",
        "analysis_engine": "分析引擎",
        "methodology_title": "⚙ 方法論說明",
        "methodology_text": "本報告基於 prism 對 {website} 的網站爬取內容（Content Studio）及 AI 答案引擎可見度追蹤數據自動生成。網站內容分析通過 LLM 對已爬取頁面進行結構化審計；Schema 檢測依賴於文字內容解析，未經 HTML 原始碼掃描驗證。可見度數據來自 prism 對多個答案引擎（ChatGPT、Claude、Gemini 等）的持續追蹤。",
        "llm_failed": "⚠ LLM 分析未能完成",
        "llm_no_key": "請在「設定 → 引擎金鑰」中設定 API 金鑰後重新生成報告。",
        "section_overview": "總覽",
        "kpi_visibility": "品牌可見度",
        "kpi_prompts": "追蹤提問",
        "kpi_runs": "總評估數",
        "kpi_citations": "引用來源數",
        "executive_summary": "執行摘要",
        "section_visibility": "AI 搜尋可見度基線",
        "prompt_col": "提問",
        "tags_col": "標籤",
        "runs_col": "評估次數",
        "visibility_col": "可見度",
        "no_tracking_data": "尚無追蹤數據。",
        "sov_title": "聲量佔比",
        "brand_col": "品牌",
        "mentions_col": "提及次數",
        "share_col": "佔比",
        "avg_pos_col": "平均排名",
        "you_tag": "(您)",
        "gaps_title": "成長機會",
        "gaps_desc": "競爭者出現但 {brand} 未出現的提問：",
        "competitors_present": "出現的競爭者",
        "section_content_audit": "網站內容審計",
        "content_audit_desc": "基於 Content Studio 爬取的 {n} 個頁面進行分析。",
        "page_inventory": "頁面清單與分類",
        "url_col": "URL",
        "title_col": "標題",
        "category_col": "分類",
        "quality_col": "質量",
        "assessment_col": "評估",
        "quality_good": "良好",
        "quality_medium": "中等",
        "quality_thin": "薄弱",
        "fact_density": "事實密度分析",
        "fact_density_label": "可驗證事實佔比（排除營銷宣傳語）",
        "missing_content": "缺失的關鍵內容類型",
        "content_type_col": "內容類型",
        "impact_col": "影響程度",
        "note_col": "說明",
        "severity_high": "高",
        "severity_medium": "中",
        "severity_low": "低",
        "brand_consistency": "品牌一致性",
        "brand_consistent": "✓ 品牌名稱在網站內使用一致。",
        "brand_inconsistent": "⚠ 發現品牌名稱不一致：",
        "freshness": "時效性信號",
        "freshness_ok": "✓ 網站時效性信號正常。",
        "section_scoring": "五大指標評分",
        "dim_intent_match": "搜尋意圖匹配度",
        "dim_citeability": "內容可引用度",
        "dim_authority": "權威資產儲備",
        "dim_technical": "技術 SEO 基礎",
        "dim_trust": "信任信號",
        "section_competitors": "競爭者分析",
        "section_citations": "引用來源分析",
        "top_domains": "熱門引用網域",
        "domain_col": "網域",
        "citations_col": "引用次數",
        "top_urls": "熱門引用頁面",
        "section_roadmap": "優化路線圖",
        "priority_immediate": "即時",
        "priority_short_term": "短期",
        "priority_long_term": "長期",
        "effort": "努力",
        "impact": "影響",
        "effort_low": "低",
        "effort_medium": "中",
        "effort_high": "高",
        "impact_low": "低",
        "impact_medium": "中",
        "impact_high": "高",
        "section_engines": "引擎分佈",
        "footer_text": "由 prism-geo 自動生成 · {date} · 數據範圍：過去 {days} 天",
        "footer_note": "此報告基於 Content Studio 爬取內容與 AI 可見度追蹤數據自動生成，所有評分均為系統評估。",
    },
    "en": {
        "report_title": "GEO (Generative Engine Optimization) Website Audit Report",
        "audit_date": "Audit Date",
        "audit_scope": "Audit Scope",
        "scope_desc": "Full-site crawl + AI visibility tracking data",
        "analysis_engine": "Analysis Engine(s)",
        "methodology_title": "⚙ Methodology Note",
        "methodology_text": "This report is auto-generated by prism based on crawled website content (Content Studio) from {website} and AI answer-engine visibility tracking data. Website content analysis is performed via LLM structured audit of crawled pages; Schema detection relies on text-based content parsing without raw HTML source-code scanning. Visibility data comes from prism's continuous tracking across multiple answer engines (ChatGPT, Claude, Gemini, etc.).",
        "llm_failed": "⚠ LLM Analysis Incomplete",
        "llm_no_key": "Set an API key under Settings → Engine Keys and regenerate the report.",
        "section_overview": "Overview",
        "kpi_visibility": "Brand Visibility",
        "kpi_prompts": "Tracked Prompts",
        "kpi_runs": "Total Evaluations",
        "kpi_citations": "Citation Sources",
        "executive_summary": "Executive Summary",
        "section_visibility": "AI Search Visibility Baseline",
        "prompt_col": "Prompt",
        "tags_col": "Tags",
        "runs_col": "Evaluations",
        "visibility_col": "Visibility",
        "no_tracking_data": "No tracking data yet.",
        "sov_title": "Share of Voice",
        "brand_col": "Brand",
        "mentions_col": "Mentions",
        "share_col": "Share",
        "avg_pos_col": "Avg. Position",
        "you_tag": "(You)",
        "gaps_title": "Growth Opportunities",
        "gaps_desc": "Prompts where competitors appear but {brand} does not:",
        "competitors_present": "competitors present",
        "section_content_audit": "Website Content Audit",
        "content_audit_desc": "Analysis based on {n} pages crawled via Content Studio.",
        "page_inventory": "Page Inventory & Classification",
        "url_col": "URL",
        "title_col": "Title",
        "category_col": "Category",
        "quality_col": "Quality",
        "assessment_col": "Assessment",
        "quality_good": "Good",
        "quality_medium": "Medium",
        "quality_thin": "Thin",
        "fact_density": "Fact Density Analysis",
        "fact_density_label": "Verifiable facts ratio (excludes marketing fluff)",
        "missing_content": "Missing Key Content Types",
        "content_type_col": "Content Type",
        "impact_col": "Impact",
        "note_col": "Note",
        "severity_high": "High",
        "severity_medium": "Medium",
        "severity_low": "Low",
        "brand_consistency": "Brand Consistency",
        "brand_consistent": "✓ Brand name used consistently across the site.",
        "brand_inconsistent": "⚠ Brand name inconsistencies found:",
        "freshness": "Freshness Signals",
        "freshness_ok": "✓ Website freshness signals are adequate.",
        "section_scoring": "Five-Dimension Scoring",
        "dim_intent_match": "Search Intent Match",
        "dim_citeability": "Content Citeability",
        "dim_authority": "Authority Assets",
        "dim_technical": "Technical SEO Foundation",
        "dim_trust": "Trust Signals",
        "section_competitors": "Competitor Analysis",
        "section_citations": "Citation Source Analysis",
        "top_domains": "Top Cited Domains",
        "domain_col": "Domain",
        "citations_col": "Citations",
        "top_urls": "Top Cited Pages",
        "section_roadmap": "Optimization Roadmap",
        "priority_immediate": "Immediate",
        "priority_short_term": "Short-Term",
        "priority_long_term": "Long-Term",
        "effort": "Effort",
        "impact": "Impact",
        "effort_low": "Low",
        "effort_medium": "Medium",
        "effort_high": "High",
        "impact_low": "Low",
        "impact_medium": "Medium",
        "impact_high": "High",
        "section_engines": "Engine Distribution",
        "footer_text": "Generated by prism-geo · {date} · Data range: last {days} days",
        "footer_note": "This report is auto-generated based on Content Studio crawled content and AI visibility tracking data. All scores are system assessments.",
    },
}


def _get_llm():
    """Return (api_key, base_url, model) from the first active engine."""
    engines = keystore.active_engines()
    if not engines:
        return None, None, None
    e = engines[0]
    return e["api_key"], e.get("base_url", ""), e.get("model", "")


def build_audit_data(tenant_id: int, days: int = 30) -> dict | None:
    """Gather all data needed for the audit: visibility metrics + crawled site content."""
    with connect() as conn:
        own = conn.execute(
            "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1", (tenant_id,)
        ).fetchone()
        if not own:
            return None
        own = dict(own)

        competitors = [
            dict(c) for c in conn.execute(
                "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 0 ORDER BY name",
                (tenant_id,),
            ).fetchall()
        ]

        sites = [
            dict(s) for s in conn.execute(
                "SELECT * FROM sites WHERE tenant_id = ? AND status = 'ready' ORDER BY last_crawled DESC",
                (tenant_id,),
            ).fetchall()
        ]

        pages_by_site = {}
        for site in sites:
            pages = [
                dict(p) for p in conn.execute(
                    "SELECT id, url, path, title, headings, content FROM pages WHERE site_id = ? ORDER BY path",
                    (site["id"],),
                ).fetchall()
            ]
            pages_by_site[site["id"]] = pages

        tenant_row = conn.execute(
            "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        tenant = dict(tenant_row) if tenant_row else {"name": own["name"], "website": ""}

    # Visibility data (need a fresh connection since we closed the last one)
    with connect() as conn:
        vis_data = queries.report_data(conn, tenant_id, days)

    return {
        "brand": own,
        "tenant": tenant,
        "competitors": competitors,
        "sites": sites,
        "pages_by_site": pages_by_site,
        "visibility": vis_data,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "days": days,
    }


def _build_content_summary(pages: list[dict], max_per_site: int = 30) -> str:
    """Build a compact text summary of crawled pages for the LLM."""
    lines = []
    for i, p in enumerate(pages[:max_per_site]):
        content_preview = (p.get("content") or "")[:400].replace("\n", " ")
        headings = (p.get("headings") or "")[:200]
        lines.append(
            f"[Page {i+1}] URL: {p['url']}\n"
            f"Title: {p.get('title', '')}\n"
            f"Headings: {headings}\n"
            f"Content preview: {content_preview}\n"
        )
    return "\n---\n".join(lines)


def _call_llm(api_key: str, base_url: str, model: str, prompt: str) -> str:
    """Make an LLM call, return text response."""
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    resp = httpx.post(
        url,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": AUDIT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.5,
            "max_tokens": 8192,
        },
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse_llm_json(text: str) -> dict:
    """Extract JSON object from LLM response (may have markdown fences)."""
    text = text.strip()
    # Try code-fenced JSON first: ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare JSON — find the outermost balanced { } pair
    start = text.find("{")
    if start == -1:
        return {"error": "Failed to parse LLM response", "raw": text[:500]}
    depth = 0
    end = -1
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {"error": "Failed to parse LLM response", "raw": text[:500]}


def analyze(tenant_id: int, days: int = 30, lang: str = "zh-TW") -> dict:
    """Run the full audit analysis. Returns complete data dict for template rendering."""
    data = build_audit_data(tenant_id, days)
    if data is None:
        return {"error": "No brand configured for this tenant."}
    data["lang"] = lang
    data["tr"] = lambda k, **fmt: _T.get(lang, _T["en"])[k].format(**fmt) if fmt else _T.get(lang, _T["en"])[k]

    api_key, base_url, model = _get_llm()
    if not api_key:
        data["analysis"] = {"error": "No LLM API key configured."}
        data["no_llm"] = True
        return data

    # Build content summary from all crawled sites
    all_pages = []
    for site_id, pages in data["pages_by_site"].items():
        all_pages.extend(pages)

    content_summary = _build_content_summary(all_pages)

    # Build visibility summary
    vis = data["visibility"]
    vis_summary = ""
    if vis:
        vis_summary = (
            f"Visibility: {vis.get('visibility', 0):.0f}%\n"
            f"Prompts tracked: {vis.get('prompts_count', 0)}\n"
            f"Evaluations: {vis.get('runs', 0)}\n"
            f"Citations: {vis.get('citations', 0)}\n"
            f"Competitors: {', '.join(c['name'] for c in data['competitors'])}\n"
            f"Share of voice top brands: "
            + ", ".join(
                f"{s['name']} ({s['mentions']} mentions)"
                for s in (vis.get("sov", []) or [])[:5]
            )
        )

    prompt = f"""Analyze this brand for a GEO audit report.

BRAND: {data['brand']['name']}
WEBSITE: {data['tenant'].get('website', '')}
PAGES CRAWLED: {len(all_pages)}

=== CRAWLED WEBSITE CONTENT ===
{content_summary}

=== AI VISIBILITY DATA ===
{vis_summary}

Analyze the website content and visibility data. Return JSON per the system prompt.
Focus on: page quality, fact density, missing content types, brand consistency,
freshness signals, five-dimension scoring, competitor insights, and recommendations."""

    try:
        llm_response = _call_llm(api_key, base_url, model, prompt)
        analysis = _parse_llm_json(llm_response)
        if analysis.get("error"):
            print(f"[audit] LLM parse failed: {llm_response[:300]}", flush=True)
        data["analysis"] = analysis
    except Exception as e:
        print(f"[audit] LLM call failed: {e}", flush=True)
        data["analysis"] = {"error": str(e)}

    return data


def generate_pdf(tenant_id: int, days: int = 30, lang: str = "zh-TW") -> bytes | None:
    """Generate PDF bytes for the audit report."""
    from jinja2 import Environment, FileSystemLoader

    data = analyze(tenant_id, days, lang)
    if data.get("error") and not data.get("analysis"):
        return None

    env = Environment(loader=FileSystemLoader(
        str(__import__("pathlib").Path(__file__).resolve().parent / "templates")
    ))
    template = env.get_template("audit_report.html")
    html_str = template.render(**data)

    from weasyprint import HTML
    return HTML(string=html_str).write_pdf()
