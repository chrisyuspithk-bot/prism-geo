"""Comprehensive GEO audit report — combines crawled website content analysis
with visibility tracking data and LLM-generated insights. Produces PDF-ready HTML.
"""

from datetime import datetime, timezone

import httpx

from . import keystore, queries
from .db import connect

_AUDIT_EN = """\
You are an expert GEO (Generative Engine Optimization) report generator.

Your task is to produce a clear, professional, and highly actionable GEO audit report using ONLY the data provided to you. Do not invent any numbers, pages, scores, findings, or recommendations that are not supported by the given data.

### Instructions

1. Carefully read all the data supplied in the current context.

2. Generate a complete GEO report in clean markdown.

3. Follow this exact structure:

   - Title and metadata

   - Executive Summary (highlight the core gap between the brand's real authority and its AI visibility)

   - AI Visibility Baseline (include tables for prompt-level performance and Share of Voice if the data contains them)

   - Website Content Diagnosis (group issues by severity: High / Medium, and list concrete examples from the data)

   - Metric Scores (if scores are provided, keep the original numbers and add a short interpretation for each)

   - Prioritized Action Roadmap

     - This week (low effort, high impact)

     - Next 2–4 weeks

     - Medium-term

     Every recommendation must be specific and directly executable based on the given data.

   - Competitive Context (short)

   - Conclusion and suggested next measurement step

### Strict Rules

- Use only the information explicitly provided. Do not add external knowledge or assumptions.
- Prioritize actionability and clarity.
- Keep the tone factual and direct.
- Output clean markdown only. Do not include any explanatory notes about your process.

Generate the GEO report now based on the data provided."""

_AUDIT_ZH = """\
你是一位專業的 GEO（Generative Engine Optimization，生成式引擎優化）報告生成專家。

你的任務是根據提供給你的資料，產出一份清晰、專業且高度可執行的 GEO 審計報告。禁止捏造任何數字、頁面、分數、發現或建議，所有內容必須有資料支持。

### 指示

1. 仔細閱讀當前上下文中提供的所有資料。

2. 以乾淨的 Markdown 格式產出完整的 GEO 報告。

3. 嚴格按照以下結構撰寫：

   - 標題與基本資訊

   - 執行摘要（重點指出品牌真實權威與 AI 可見度之間的落差）

   - AI 可見度基線（若資料有提供，需包含提示詞表現表格與聲量佔比表格）

   - 網站內容診斷（依嚴重程度分為「高」與「中」，並列出資料中的具體例子）

   - 指標評分（若有提供分數，保留原始數字，並為每個指標加上簡短解釋）

   - 優先優化路線圖

     - 本週立即處理（低努力、高影響）

     - 未來 2–4 週

     - 中期

     每一項建議都必須具體且可直接執行，並完全基於提供的資料。

   - 競爭格局簡述

   - 結論與下一步量測建議

### 嚴格規則

- 只能使用明確提供的資料，禁止加入外部知識或任何假設。
- 優先考慮可執行性與清晰度。
- 語氣保持事實、直接。
- 只輸出乾淨的 Markdown 內容，不要加入任何關於你思考過程的說明。

現在根據提供的資料生成 GEO 報告。"""

_AUDIT_SYSTEMS = {"en": _AUDIT_EN, "zh-TW": _AUDIT_ZH}

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


# ── Page diagnostics (pre-computed before LLM prompt) ──────────────────────

# Content length threshold (chars) below which a page is flagged as thin
_THIN_THRESHOLD = 200

# Keywords that suggest a page is high-value (case study, client work, portfolio)
_HIGH_VALUE_KEYWORDS = [
    "dior", "hospital", "醫院", "science", "park", "science park", "科學園",
    "case", "案例", "project", "工程", "client", "客戶", "portfolio",
    "旗艦", "限定", "廣華", "café de coral", "大家樂", "ifc",
]

# Title→content keyword maps for mismatch detection.
# If title contains a key but content has NONE of the values, flag as mismatch.
_TITLE_CONTENT_CHECKS = [
    # (title_keywords, content_keywords, label)
    (["備份", "backup"], ["備份", "backup", "還原", "restore", "災難", "disaster"], "Backup"),
    (["終端防禦", "edr", "端點"], ["edr", "endpoint", "端點", "防禦", "antivirus", "malware"], "EDR/Endpoint"),
    (["網絡防護", "cyber", "網路安全"], ["cyber", "網絡", "網路", "firewall", "防火牆", "hkcert"], "Cybersecurity"),
    (["閉路電視", "cctv", "監察", " camera"], ["camera", "鏡頭", "cctv", "監控", "ai", "人工智能"], "CCTV/Surveillance"),
    (["保安系統", "security system"], ["security", "保安", "alarm", "警報", "access", "門禁"], "Security System"),
    (["審計", "audit"], ["audit", "審計", "檢查", "inspection", "合規", "compliance"], "Audit"),
]


def _build_page_diagnostics(pages: list[dict]) -> str:
    """Pre-compute page-level issues: thin content, title mismatches, schema gaps.

    Returns a structured text block for injection into the LLM prompt.
    The LLM uses this to write the Content Diagnosis and Action Roadmap sections.
    """
    if not pages:
        return ""

    thin_pages: list[dict] = []
    high_value_thin: list[dict] = []
    mismatches: list[dict] = []

    for p in pages:
        content = (p.get("content") or "").strip()
        title = (p.get("title") or "").strip()
        url = p.get("url", "")
        content_len = len(content)

        # 1. Thin page detection
        is_thin = content_len < _THIN_THRESHOLD
        if is_thin:
            # Check if it looks like a stub (title-only, or just a heading repeated)
            heading_only = content_len < 80 and (
                not content or content[:50] in title or title[:50] in content
            )
            thin_pages.append({
                **p,
                "content_len": content_len,
                "heading_only": heading_only,
            })

        # 2. High-value thin page — thin page whose URL/title suggests importance
        if is_thin:
            combined = (url + " " + title).lower()
            if any(kw in combined for kw in _HIGH_VALUE_KEYWORDS):
                high_value_thin.append({
                    **p,
                    "content_len": content_len,
                    "heading_only": content_len < 80,
                })

        # 3. Title-content mismatch
        if title and content_len >= 50:
            title_lower = title.lower()
            content_lower = content.lower()
            for title_kws, content_kws, label in _TITLE_CONTENT_CHECKS:
                if any(kw in title_lower for kw in title_kws):
                    if not any(kw in content_lower for kw in content_kws):
                        mismatches.append({
                            "url": url, "title": title,
                            "label": label,
                            "content_preview": content[:120],
                        })
                    break  # one label per page

    # Build the diagnostic text
    parts = []

    # ── Thin pages ──
    if thin_pages:
        parts.append(f"### Thin Pages (content < {_THIN_THRESHOLD} chars) — {len(thin_pages)} found")
        for p in thin_pages[:20]:
            stub = " [STUB: title-only, no body]" if p["heading_only"] else ""
            parts.append(
                f"- **{p['url']}** ({p['content_len']} chars){stub}\n"
                f"  Title: {p['title'][:100]}"
            )
        if len(thin_pages) > 20:
            parts.append(f"  ... and {len(thin_pages) - 20} more thin pages.")
        parts.append("")

    # ── High-value thin pages (priority flag) ──
    if high_value_thin:
        parts.append("### ⚠ HIGH SEVERITY — Thin pages on high-value URLs (case studies, clients, flagship projects)")
        for p in high_value_thin:
            stub = " [EMPTY BODY — title only, zero content]" if p["heading_only"] else ""
            parts.append(
                f"- **{p['url']}** ({p['content_len']} chars){stub}\n"
                f"  Title: {p['title'][:100]}"
            )
        parts.append("")

    # ── Title-content mismatches ──
    if mismatches:
        parts.append("### ⚠ HIGH SEVERITY — Title vs Content Mismatches")
        for m in mismatches:
            parts.append(
                f"- **{m['url']}**\n"
                f"  Title says: \"{m['title'][:100]}\"\n"
                f"  Content appears to be about: \"{m['content_preview']}\"\n"
                f"  Expected topic: {m['label']}"
            )
        parts.append("")

    # ── Schema recommendation ──
    parts.append("### Schema / Structured Data Gap")
    parts.append(
        "No JSON-LD Schema markup detected on any crawled page. "
        "This means answer engines cannot extract structured entity data "
        "(organization name, address, services, FAQ). "
        "RECOMMENDATION: Add JSON-LD Organization + LocalBusiness schema "
        "on the homepage and all core service pages. Also add FAQPage schema "
        "on service detail pages. This is low-effort, high-impact — "
        "a single <script type=\"application/ld+json\"> block per page."
    )
    parts.append("")

    return "\n".join(parts)


def _call_llm(api_key: str, base_url: str, model: str, prompt: str,
              system: str = "") -> str:
    """Make an LLM call, return text response."""
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    resp = httpx.post(
        url,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
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


def _build_vis_summary(vis: dict | None, competitors: list) -> str:
    """Build a structured text summary of visibility data for the LLM prompt."""
    if not vis:
        return "(No visibility data available.)"

    lines = [
        f"Overall brand visibility: {vis.get('visibility', 0):.0f}%",
        f"Prompts tracked: {vis.get('prompts_count', 0)}",
        f"Total evaluations: {vis.get('runs', 0)}",
        f"Total citations: {vis.get('citations', 0)}",
        f"Competitors: {', '.join(c['name'] for c in competitors)}",
        "",
    ]

    # Prompt-level visibility
    prompt_rows = vis.get("prompt_rows", []) or []
    if prompt_rows:
        lines.append("=== Prompt Visibility ===")
        for p in prompt_rows:
            tags = f" [{p.get('tags', '')}]" if p.get("tags") else ""
            lines.append(
                f"  \"{p['text'][:100]}\"{tags} — "
                f"{p.get('runs', 0)} evaluations, {p.get('visibility', 0):.0f}% visibility"
            )
        lines.append("")

    # Share of voice
    sov = vis.get("sov", []) or []
    if sov:
        lines.append("=== Share of Voice ===")
        total = vis.get("total_mentions", 0)
        for s in sov[:10]:
            share = (s["mentions"] / total * 100) if total else 0
            marker = " ← YOUR BRAND" if s["name"] == vis.get("brand_name", "") else ""
            lines.append(
                f"  {s['name']}: {s['mentions']} mentions ({share:.0f}% share), "
                f"avg position {s.get('avg_pos', '—')}{marker}"
            )
        lines.append("")

    # Growth opportunities / gaps
    gaps = vis.get("gaps", []) or []
    if gaps:
        lines.append("=== Growth Opportunities (competitors present, you absent) ===")
        for g in gaps:
            lines.append(f"  \"{g['text'][:100]}\" — competitors: {g['competitors']}")
        lines.append("")

    # Top citation domains
    top_domains = vis.get("top_domains", []) or []
    if top_domains:
        lines.append("=== Top Citation Domains ===")
        for d in top_domains[:15]:
            lines.append(f"  {d['domain']} ({d.get('category', '')}): {d['n']} citations")
        lines.append("")

    # Top cited pages
    top_urls = vis.get("top_urls", []) or []
    if top_urls:
        lines.append("=== Top Cited Pages ===")
        for u in top_urls[:10]:
            lines.append(f"  {u['url'][:120]} ({u.get('domain', '')}): {u['n']} citations")
        lines.append("")

    # Engine distribution
    engines = vis.get("engines", []) or []
    if engines:
        lines.append("=== Engine Distribution ===")
        for e in engines:
            lines.append(f"  {e['name']}: {e['n']} evaluations")
        lines.append("")

    return "\n".join(lines)


def analyze(tenant_id: int, days: int = 30, lang: str = "zh-TW") -> dict:
    """Run the full audit analysis. Returns data dict with 'markdown' key for the report."""
    data = build_audit_data(tenant_id, days)
    if data is None:
        return {"error": "No brand configured for this tenant."}
    data["lang"] = lang
    tr_lang = _T.get(lang, _T["en"])
    data["tr"] = lambda k, **fmt: tr_lang[k].format(**fmt) if fmt else tr_lang[k]

    api_key, base_url, model = _get_llm()
    if not api_key:
        data["no_llm"] = True
        data["markdown"] = None
        return data

    all_pages = []
    for site_id, pages in data["pages_by_site"].items():
        all_pages.extend(pages)

    content_summary = _build_content_summary(all_pages)
    diagnostics = _build_page_diagnostics(all_pages)
    vis_summary = _build_vis_summary(data["visibility"], data["competitors"])

    system = _AUDIT_SYSTEMS.get(lang, _AUDIT_SYSTEMS["en"])

    prompt = f"""=== BRAND ===
Name: {data['brand']['name']}
Website: {data['tenant'].get('website', '')}
Pages crawled: {len(all_pages)}

=== CRAWLED WEBSITE CONTENT ===
{content_summary}

=== PRE-COMPUTED PAGE DIAGNOSTICS ===
These issues were found by automated analysis. You MUST include them in your report.
- List the HIGH SEVERITY issues in the Content Diagnosis section with exact URLs.
- Include the Schema gap as a "This week" action item in the Action Roadmap.
- Use the thin/mismatch findings to write concrete, URL-specific recommendations.

{diagnostics}

=== AI VISIBILITY TRACKING DATA ===
{vis_summary}"""

    try:
        data["markdown"] = _call_llm(api_key, base_url, model, prompt, system)
    except Exception as e:
        print(f"[audit] LLM call failed: {e}", flush=True)
        data["markdown"] = None
        data["llm_error"] = str(e)

    return data


def generate_pdf(tenant_id: int, days: int = 30, lang: str = "zh-TW") -> bytes | None:
    """Generate PDF bytes for the audit report — markdown rendered as styled PDF."""
    from markdown import markdown as md_to_html

    data = analyze(tenant_id, days, lang)
    if data.get("error"):
        return None

    md_text = data.get("markdown")
    if not md_text:
        return None

    body_html = md_to_html(md_text, extensions=["tables", "fenced_code"])

    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(
        str(__import__("pathlib").Path(__file__).resolve().parent / "templates")
    ))
    template = env.get_template("audit_report.html")
    html_str = template.render(body=body_html, **data)

    from weasyprint import HTML
    return HTML(string=html_str).write_pdf()
