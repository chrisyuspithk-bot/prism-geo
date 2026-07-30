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
and visibility metrics from the provided data. Write in Traditional Chinese
(繁體中文) for Chinese brands, English otherwise.

Return your analysis as a JSON object with these keys:
- "executive_summary": 2-3 paragraph overall assessment
- "content_audit": {
    "page_inventory": [{ "url","title","category":"product|about|blog|service|landing|other","quality":"good|medium|thin","note":"1-line" }],
    "fact_density": { "pct":0-100, "verifiable_examples":["specific fact"], "assessment":"1 paragraph" },
    "missing_content": [{ "type":"FAQ|case_studies|comparison_guides|testimonials|pricing|tech_specs|white_papers|schema_markup", "severity":"high|medium|low", "note":"why it matters for AI visibility" }],
    "schema_assessment": { "detected":true/false, "types_found":["..."], "note":"1-2 sentences on impact" },
    "brand_consistency": { "ok":true/false, "issues":["..."] },
    "freshness": { "ok":true/false, "last_updated_estimate":"...", "issues":["..."] }
  },
- "scoring": {
    "intent_match": { "score":0-100, "weight":25, "breakdown":"1-2 sentence assessment" },
    "citeability": { "score":0-100, "weight":25, "breakdown":"1-2 sentence assessment" },
    "authority": { "score":0-100, "weight":20, "breakdown":"1-2 sentence assessment" },
    "visibility": { "score":0-100, "weight":20, "breakdown":"1-2 sentence assessment" },
    "consistency": { "score":0-100, "weight":10, "breakdown":"1-2 sentence assessment" }
  },
- "competitor_analysis": {
    "overview": "1 paragraph competitive landscape",
    "per_competitor": [{ "name":"Name","strengths":["..."],"weaknesses":["..."],"opportunity":"how to differentiate" }]
  },
- "platform_analysis": {
    "overview": "1 paragraph cross-engine summary",
    "engines": [{ "name":"engine name","assessment":"1 sentence","recommendation":"platform-specific tip" }]
  },
- "third_party_signals": {
    "note": "Automated detection is limited. Manual audit recommended for:",
    "checklist": ["GBP listing","industry directories","social media profiles","media/PR coverage","backlink profile"]
  },
- "recommendations": {
    "immediate": [{ "action":"...","effort":"low|medium|high","impact":"high|medium|low" }],
    "short_term": [{ "action":"...","effort":"low|medium|high","impact":"high|medium|low" }],
    "long_term": [{ "action":"...","effort":"low|medium|high","impact":"high|medium|low" }],
    "effort_impact_matrix": { "quick_wins":["..."],"major_projects":["..."],"fill_ins":["..."],"money_pits":["..."] },
    "content_templates": [{ "type":"FAQ|case_study|comparison|report","headline":"...","structure":["bullet","bullet","bullet"] }],
    "monitoring": { "metrics":[{ "name":"...","tool":"...","frequency":"weekly|monthly|quarterly" }] }
  }
Return ONLY valid JSON, no markdown fences, no trailing commas."""


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
        "pages_by_site": {site["domain"]: pgs for site in sites if (pgs := pages_by_site.get(site["id"], []))},
        "all_pages": [p for pgs in pages_by_site.values() for p in pgs],
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
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"error": "Failed to parse LLM response", "raw": text[:500]}


def analyze(tenant_id: int, days: int = 30) -> dict:
    """Run the full audit analysis. Returns complete data dict for template rendering."""
    data = build_audit_data(tenant_id, days)
    if data is None:
        return {"error": "No brand configured for this tenant."}

    api_key, base_url, model = _get_llm()
    if not api_key:
        data["analysis"] = {"error": "No LLM API key configured."}
        data["no_llm"] = True
        return data

    # Build content summary from all crawled sites
    all_pages = data.get("all_pages", [])
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
        data["analysis"] = analysis
    except Exception as e:
        data["analysis"] = {"error": str(e)}

    return data


def generate_pdf(tenant_id: int, days: int = 30) -> bytes | None:
    """Generate PDF bytes for the audit report."""
    from jinja2 import Environment, FileSystemLoader

    data = analyze(tenant_id, days)
    if data.get("error") and not data.get("analysis"):
        return None

    env = Environment(loader=FileSystemLoader(
        str(__import__("pathlib").Path(__file__).resolve().parent / "templates")
    ))
    template = env.get_template("audit_report.html")
    html_str = template.render(**data)

    from weasyprint import HTML
    return HTML(string=html_str).write_pdf()
