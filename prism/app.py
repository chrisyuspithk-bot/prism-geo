"""prism — multi-tenant AI visibility tracking (GEO/AEO). FastAPI web app.

prism is the GEO provider: one deployment hosts many clients. Each client
(tenant) has its own domain, competitors, and prompt set; every dashboard and
evaluation is scoped to the tenant selected in the sidebar. Engine API keys are
configured once, centrally, by the operator and shared across all tenants.
"""

import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import audit_report, i18n, jobs, keystore, queries, report, scheduler, workspace
from . import crawler, chunk, drafts, embeddings, rag
from .db import connect, init_db, q, q1
from .onboarding import analyze_website, generate_prompts

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _tz_display(utc_str: str) -> str:
    """Convert a UTC datetime string to GMT+8 display format."""
    if not utc_str:
        return ""
    from datetime import datetime, timezone, timedelta
    try:
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return utc_str
    local = dt.astimezone(timezone(timedelta(hours=8)))
    return local.strftime("%Y-%m-%d %H:%M")


templates.env.filters["tz"] = _tz_display


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    jobs.recover_stale_jobs()
    jobs.ensure_worker()
    with connect() as conn:
        conn.execute("UPDATE sites SET status = 'failed', crawl_error = 'Server restarted during crawl' WHERE status = 'crawling'")
    scheduler.ensure_scheduler()
    yield


app = FastAPI(title="prism", description="AI visibility tracking (GEO/AEO)", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


# --- Tenant resolution ---------------------------------------------------------

def _tenant(request: Request) -> dict:
    """The active client workspace, from ?tenant= or cookie, defaulting to first."""
    with connect() as conn:
        tenants = [dict(t) for t in workspace.list_tenants(conn)]
        tid = request.query_params.get("tenant")
        if tid and any(t["id"] == int(tid) for t in tenants):
            tenant_id = int(tid)
        else:
            cookie = request.cookies.get("prism_tenant")
            if cookie and any(t["id"] == int(cookie) for t in tenants):
                tenant_id = int(cookie)
            else:
                tenant_id = tenants[0]["id"] if tenants else workspace.default_tenant_id(conn)
        current = next((t for t in tenants if t["id"] == tenant_id),
                       dict(workspace.get_tenant(conn, tenant_id)))
    return {"id": tenant_id, "current": current, "all": tenants}


def _resolve_lang(request: Request) -> str:
    """Resolve language: ?lang= query param > prism_lang cookie > default zh-TW."""
    qp = request.query_params.get("lang")
    if qp and qp in i18n.LANGUAGES:
        return qp
    cookie = request.cookies.get("prism_lang")
    if cookie and cookie in i18n.LANGUAGES:
        return cookie
    return "zh-TW"


def ctx(request: Request, **kwargs) -> dict:
    tenant = _tenant(request)
    lang = _resolve_lang(request)
    with connect() as conn:
        models_list = [dict(m) for m in queries.models(conn)]
        has_brand = q1(conn, "SELECT 1 FROM brands WHERE tenant_id = ? AND is_own = 1",
                       (tenant["id"],)) is not None
    return {"request": request, "models": models_list, "tenant": tenant,
            "PRISM_KEY": keystore.has_any_key(), "lang": lang,
            "languages": i18n.LANGUAGES, "has_brand": has_brand,
            "t": lambda key, **fmt: i18n.t(lang, key, **fmt), **kwargs}


def _scoped(response: RedirectResponse, request: Request) -> RedirectResponse:
    """Persist the chosen tenant as a cookie on redirects."""
    tid = request.query_params.get("tenant") or request.cookies.get("prism_tenant")
    if tid:
        response.set_cookie("prism_tenant", tid)
    return response


def _filters(request: Request) -> tuple[int, int | None]:
    days = int(request.query_params.get("days", 30))
    model_id = request.query_params.get("model")
    return days, int(model_id) if model_id else None


# --- Client (tenant) management ------------------------------------------------

@app.get("/tenants", response_class=HTMLResponse)
def tenants_page(request: Request):
    tenant = _tenant(request)
    with connect() as conn:
        rows = []
        for t in tenant["all"]:
            stats = q1(conn, "SELECT COUNT(*) n FROM runs WHERE tenant_id = ?", (t["id"],))
            prompts = q1(conn, "SELECT COUNT(*) n FROM prompts WHERE tenant_id = ? AND active = 1",
                         (t["id"],))
            comps = q1(conn, "SELECT COUNT(*) n FROM brands WHERE tenant_id = ? AND is_own = 0",
                       (t["id"],))
            rows.append({**t, "runs": stats["n"], "prompts": prompts["n"],
                         "competitors": comps["n"]})
    return templates.TemplateResponse(
        request, "tenants.html",
        context=ctx(request, page="tenants", tenants=rows))


@app.post("/tenants")
async def create_client(request: Request, name: str = Form(...), website: str = Form("")):
    tenant_id = workspace.create_tenant(name.strip(), website.strip())
    resp = RedirectResponse(f"/setup?tenant={tenant_id}", status_code=303)
    resp.set_cookie("prism_tenant", str(tenant_id))
    return resp


@app.post("/tenants/{tenant_id}/delete")
def delete_client(request: Request, tenant_id: int):
    workspace.delete_tenant(tenant_id)
    with connect() as conn:
        remaining = workspace.default_tenant_id(conn)
    resp = RedirectResponse(f"/tenants?tenant={remaining}", status_code=303)
    resp.set_cookie("prism_tenant", str(remaining))
    return resp


@app.post("/tenant/select")
def select_tenant(request: Request, tenant: int = Form(...), back: str = Form("/")):
    resp = RedirectResponse(back, status_code=303)
    resp.set_cookie("prism_tenant", str(tenant))
    return resp


# --- Setup / onboarding --------------------------------------------------------

@app.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    tenant = _tenant(request)
    with connect() as conn:
        own = workspace.own_brand(conn, tenant["id"])
        competitors = workspace.competitors(conn, tenant["id"])
    return templates.TemplateResponse(
        request, "setup.html",
        context=ctx(request, page="setup", own=own,
                    competitors=[c["name"] for c in competitors]))


@app.post("/setup")
async def setup_save(request: Request, name: str = Form(...),
                     website: str = Form(""), competitors: str = Form("")):
    tenant = _tenant(request)
    workspace.update_tenant(tenant["id"], name.strip(), website.strip())
    comp_list = [c.strip() for c in competitors.replace("\n", ",").split(",") if c.strip()]
    with connect() as conn:
        existing = {c["name"].lower() for c in workspace.competitors(conn, tenant["id"])}
    for comp in comp_list:
        if comp.lower() not in existing:
            workspace.add_competitor(tenant["id"], comp)

    created = 0
    site = await analyze_website(website.strip())
    lang = _resolve_lang(request)
    prompts = await generate_prompts(name.strip(), comp_list, site, lang=lang)
    with connect() as conn:
        for p in prompts:
            conn.execute("INSERT INTO prompts (text, tags, tenant_id) VALUES (?, ?, ?)",
                         (p["text"], p["tag"], tenant["id"]))
            created += 1
    return RedirectResponse(f"/setup/review?tenant={tenant['id']}&created={created}",
                            status_code=303)


@app.get("/setup/review", response_class=HTMLResponse)
def setup_review(request: Request, created: int = 0):
    tenant = _tenant(request)
    with connect() as conn:
        own = workspace.own_brand(conn, tenant["id"])
        competitors = workspace.competitors(conn, tenant["id"])
        prompts = q(conn, "SELECT * FROM prompts WHERE active = 1 AND tenant_id = ? "
                          "ORDER BY id DESC LIMIT 15", (tenant["id"],))
    return templates.TemplateResponse(
        request, "setup_review.html",
        context=ctx(request, page="setup", brand=own, created=created,
                    competitors=[c["name"] for c in competitors],
                    prompts=[dict(p) for p in prompts]))


@app.post("/setup/run")
def setup_run(request: Request):
    """Queue the first full evaluation for the active tenant, show progress."""
    tenant = _tenant(request)
    with connect() as conn:
        total = q1(conn, "SELECT COUNT(*) n FROM prompts WHERE active = 1 AND tenant_id = ?",
                   (tenant["id"],))["n"]
    job_id = jobs.create_job("run_all", {"tenant_id": tenant["id"]}, total=total)
    return RedirectResponse(f"/jobs/{job_id}?back=/", status_code=303)


# --- Competitor management -----------------------------------------------------

@app.post("/settings/competitors")
def add_competitor(request: Request, name: str = Form(...)):
    tenant = _tenant(request)
    workspace.add_competitor(tenant["id"], name.strip())
    return RedirectResponse("/settings/brand", status_code=303)


@app.post("/settings/competitors/{competitor_id}/delete")
def delete_competitor(request: Request, competitor_id: int):
    tenant = _tenant(request)
    workspace.remove_competitor(tenant["id"], competitor_id)
    return RedirectResponse("/settings/brand", status_code=303)


@app.get("/settings/brand", response_class=HTMLResponse)
def settings_brand(request: Request):
    tenant = _tenant(request)
    with connect() as conn:
        own = workspace.own_brand(conn, tenant["id"])
        competitors = [dict(c) for c in workspace.competitors(conn, tenant["id"])]
    return templates.TemplateResponse(
        request, "settings_brand.html",
        context=ctx(request, page="settings-brand", own=own, competitors=competitors))


# --- Dashboards (all tenant-scoped) --------------------------------------------

@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    tenant = _tenant(request)
    days, model_id = _filters(request)
    with connect() as conn:
        data = queries.overview(conn, tenant["id"], days, model_id)
        sov = queries.share_of_voice_page(conn, tenant["id"], days, model_id)
    return templates.TemplateResponse(
        request, "overview.html", context=ctx(request, page="overview", data=data, sov=sov, days=days))


@app.get("/visibility", response_class=HTMLResponse)
def visibility(request: Request):
    tenant = _tenant(request)
    days, model_id = _filters(request)
    with connect() as conn:
        data = queries.visibility_page(conn, tenant["id"], days, model_id)
    return templates.TemplateResponse(
        request, "visibility.html", context=ctx(request, page="visibility", data=data, days=days))


@app.get("/report/download")
def download_report(request: Request, period: str = "monthly"):
    tenant = _tenant(request)
    lang = _resolve_lang(request)
    days, model_id = _filters(request)
    tname = tenant["current"].get("name", "")
    tid = tenant["id"]
    with connect() as conn:
        own = workspace.own_brand(conn, tid)
        brand_name = own["name"] if own else tname or "Brand"
    try:
        pdf = report.generate(tid, tname, brand_name, period, model_id, lang=lang)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    filename = f"prism-geo-{period}-{brand_name.lower().replace(' ', '-')}.pdf"
    from fastapi.responses import Response
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/share-of-voice", response_class=HTMLResponse)
def share_of_voice(request: Request):
    tenant = _tenant(request)
    days, model_id = _filters(request)
    with connect() as conn:
        data = queries.share_of_voice_page(conn, tenant["id"], days, model_id)
    return templates.TemplateResponse(
        request, "share_of_voice.html", context=ctx(request, page="share-of-voice", data=data, days=days))


@app.get("/citations", response_class=HTMLResponse)
def citations(request: Request):
    tenant = _tenant(request)
    days, model_id = _filters(request)
    domain_page = max(1, int(request.query_params.get("dp", "1")))
    url_page = max(1, int(request.query_params.get("up", "1")))
    with connect() as conn:
        data = queries.citations_page(conn, tenant["id"], days, model_id,
                                      domain_page=domain_page, url_page=url_page)
    return templates.TemplateResponse(
        request, "citations.html", context=ctx(request, page="citations", data=data, days=days))


@app.get("/opportunities", response_class=HTMLResponse)
def opportunities(request: Request):
    tenant = _tenant(request)
    days, model_id = _filters(request)
    with connect() as conn:
        data = queries.opportunities_page(conn, tenant["id"], days, model_id)
    return templates.TemplateResponse(
        request, "opportunities.html", context=ctx(request, page="opportunities", data=data, days=days))


@app.get("/prompts/{prompt_id}", response_class=HTMLResponse)
def prompt_detail(request: Request, prompt_id: int):
    days, _ = _filters(request)
    citations_page = max(1, int(request.query_params.get("cit_page", "1")))
    runs_page = max(1, int(request.query_params.get("run_page", "1")))
    with connect() as conn:
        data = queries.prompt_detail(conn, prompt_id, days,
                                     citations_page=citations_page,
                                     runs_page=runs_page)
    if data is None:
        return RedirectResponse("/visibility", status_code=303)
    return templates.TemplateResponse(
        request, "prompt_detail.html", context=ctx(request, page="visibility", data=data, days=days))


@app.post("/prompts/{prompt_id}/run")
def run_now(prompt_id: int):
    """Queue one prompt to run in the background, redirect to its progress."""
    job_id = jobs.create_job("run_prompt", {"prompt_id": prompt_id}, total=1)
    return RedirectResponse(f"/jobs/{job_id}?back=/prompts/{prompt_id}", status_code=303)


@app.post("/run-all")
def run_all_now(request: Request):
    """Queue a full evaluation (this tenant's prompts x engines) in the background."""
    if not keystore.has_any_key():
        return RedirectResponse("/settings/keys?need=1", status_code=303)
    tenant = _tenant(request)
    with connect() as conn:
        prompts = q1(conn, "SELECT COUNT(*) n FROM prompts WHERE active = 1 AND tenant_id = ?",
                     (tenant["id"],))["n"]
    total = prompts * len(keystore.active_engines())
    job_id = jobs.create_job("run_all", {"tenant_id": tenant["id"]}, total=total)
    return RedirectResponse(f"/jobs/{job_id}?back=/visibility", status_code=303)


# --- Background jobs -----------------------------------------------------------

@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: int, back: str = "/"):
    job = jobs.get_job(job_id)
    if job is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "job.html", context=ctx(request, page="", job=job, back=back))


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int):
    job = jobs.get_job(job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(job)


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, back: str = "/"):
    jobs.request_cancel(job_id)
    return RedirectResponse(f"/jobs/{job_id}?back={back}", status_code=303)


# --- Engine keys (operator-level, shared across tenants) -----------------------

@app.get("/settings/keys", response_class=HTMLResponse)
def settings_keys(request: Request):
    with connect() as conn:
        enabled = conn.execute(
            "SELECT value FROM settings WHERE key = 'schedule_enabled'").fetchone()
        hour = conn.execute(
            "SELECT value FROM settings WHERE key = 'schedule_hour'").fetchone()
        emb = conn.execute(
            "SELECT value FROM settings WHERE key = 'embedding_api_key'").fetchone()
    emb_key = (emb["value"] or "").strip() if emb else os.environ.get("JINA_API_KEY", "")
    emb_source = "ui" if (emb and emb["value"]) else ("env" if os.environ.get("JINA_API_KEY") else "")
    return templates.TemplateResponse(
        request, "settings_keys.html",
        context=ctx(request, page="settings-keys", providers=keystore.provider_status(),
                    embedding_key=emb_key, embedding_source=emb_source,
                    schedule_enabled=(enabled["value"] == "1") if enabled else True,
                    schedule_hour=int(hour["value"]) if hour else 2))


@app.post("/settings/keys")
def save_keys(request: Request, provider: str = Form(...),
              api_key: str = Form(""), base_url: str = Form(""),
              model: str = Form(""), enabled: str = Form("0")):
    if provider == "embedding":
        keystore.set_value("embedding_api_key", api_key.strip() or "")
        return RedirectResponse("/settings/keys?saved=1", status_code=303)
    # Built-in providers
    if provider in keystore.PROVIDERS:
        if api_key.strip():
            keystore.set_value(f"{provider}_api_key", api_key.strip())
        if base_url.strip():
            keystore.set_value(f"{provider}_base_url", base_url.strip())
        if model.strip():
            keystore.set_value(f"{provider}_model", model.strip())
        keystore.set_value(f"{provider}_enabled", "1" if enabled == "1" else "0")
        return RedirectResponse("/settings/keys?saved=1", status_code=303)
    # Dynamic custom engines (e.g. custom_1, custom_2)
    import re
    m = re.match(r'^custom_(\d+)$', provider)
    if m:
        prefix = f"custom_{m.group(1)}"
        if api_key.strip():
            keystore.set_value(f"{prefix}_api_key", api_key.strip())
        if base_url.strip():
            keystore.set_value(f"{prefix}_base_url", base_url.strip())
        if model.strip():
            keystore.set_value(f"{prefix}_model", model.strip())
        keystore.set_value(f"{prefix}_enabled", "1" if enabled == "1" else "0")
        return RedirectResponse("/settings/keys?saved=1", status_code=303)
    # New custom engine from "+ Add Engine" dropdown
    if provider == "__custom__":
        cid = keystore.add_custom_engine()
        prefix = f"custom_{cid}"
        if api_key.strip():
            keystore.set_value(f"{prefix}_api_key", api_key.strip())
        if base_url.strip():
            keystore.set_value(f"{prefix}_base_url", base_url.strip())
        if model.strip():
            keystore.set_value(f"{prefix}_model", model.strip())
        keystore.set_value(f"{prefix}_enabled", "1" if enabled == "1" else "0")
    return RedirectResponse("/settings/keys?saved=1", status_code=303)


@app.post("/settings/keys/{provider}/clear")
def clear_key(provider: str):
    if provider == "embedding":
        keystore.set_value("embedding_api_key", "")
        return RedirectResponse("/settings/keys", status_code=303)
    if provider in keystore.PROVIDERS:
        for suffix in ("api_key", "base_url", "model", "enabled"):
            keystore.set_value(f"{provider}_{suffix}", "")
        return RedirectResponse("/settings/keys", status_code=303)
    # Dynamic custom engine
    import re
    m = re.match(r'^custom_(\d+)$', provider)
    if m:
        keystore.remove_custom_engine(int(m.group(1)))
    return RedirectResponse("/settings/keys", status_code=303)


@app.post("/settings/schedule")
def save_schedule(enabled: str = Form("0"), hour: str = Form("2")):
    keystore.set_value("schedule_enabled", "1" if enabled == "1" else "0")
    keystore.set_value("schedule_hour", hour)
    return RedirectResponse("/settings/keys?saved=1", status_code=303)


# --- Prompt management ----------------------------------------------------------

@app.post("/prompts")
def add_prompt(request: Request, text: str = Form(...), tags: str = Form("")):
    tenant = _tenant(request)
    with connect() as conn:
        conn.execute("INSERT INTO prompts (text, tags, tenant_id) VALUES (?, ?, ?)",
                     (text.strip(), tags.strip(), tenant["id"]))
    return RedirectResponse("/settings/prompts", status_code=303)


@app.post("/prompts/{prompt_id}/delete")
def delete_prompt(request: Request, prompt_id: int):
    tenant = _tenant(request)
    with connect() as conn:
        conn.execute("UPDATE prompts SET active = 0 WHERE id = ? AND tenant_id = ?",
                     (prompt_id, tenant["id"]))
    return RedirectResponse("/settings/prompts", status_code=303)


@app.post("/prompts/{prompt_id}/update")
def update_prompt(request: Request, prompt_id: int,
                  text: str = Form(...), tags: str = Form("")):
    tenant = _tenant(request)
    with connect() as conn:
        conn.execute("UPDATE prompts SET text = ?, tags = ? WHERE id = ? AND tenant_id = ?",
                     (text, tags, prompt_id, tenant["id"]))
    return RedirectResponse("/settings/prompts", status_code=303)


@app.get("/settings/prompts", response_class=HTMLResponse)
def settings_prompts(request: Request):
    tenant = _tenant(request)
    with connect() as conn:
        total = q1(conn, "SELECT COUNT(*) AS n FROM runs WHERE tenant_id = ?", (tenant["id"],))["n"]
        rows = q(conn,
                 "SELECT p.*, (SELECT COUNT(*) FROM runs r WHERE r.prompt_id = p.id) AS runs"
                 " FROM prompts p WHERE p.tenant_id = ? AND p.active = 1 ORDER BY p.id", (tenant["id"],))
    return templates.TemplateResponse(
        request, "settings_prompts.html",
        context=ctx(request, page="settings-prompts", prompts=[dict(r) for r in rows],
                    total_runs=total))


# --- JSON API (tenant-scoped) ---------------------------------------------------

@app.get("/api/visibility")
def api_visibility(request: Request, days: int = 30):
    tenant = _tenant(request)
    with connect() as conn:
        return JSONResponse(queries.visibility_page(conn, tenant["id"], days))


@app.get("/api/share-of-voice")
def api_sov(request: Request, days: int = 30):
    tenant = _tenant(request)
    with connect() as conn:
        return JSONResponse(queries.share_of_voice_page(conn, tenant["id"], days))


@app.get("/api/citations")
def api_citations(request: Request, days: int = 30):
    tenant = _tenant(request)
    with connect() as conn:
        return JSONResponse(queries.citations_page(conn, tenant["id"], days))


# --- Content Studio (website crawling + RAG copy generation) ------------------

def _site_queries(tenant_id: int):
    """Return (sites list, dict of site -> pages list)."""
    with connect() as conn:
        rows = q(conn,
                 "SELECT * FROM sites WHERE tenant_id = ? ORDER BY created_at DESC",
                 (tenant_id,))
        sites = [dict(r) for r in rows]
        pages_by_site = {}
        for s in sites:
            ps = q(conn,
                   """SELECT p.*, COUNT(c.id) as chunk_count
                      FROM pages p LEFT JOIN chunks c ON c.page_id = p.id
                      WHERE p.site_id = ?
                      GROUP BY p.id ORDER BY p.crawled_at""",
                   (s["id"],))
            pages_by_site[s["id"]] = [dict(p) for p in ps]
    return sites, pages_by_site


@app.get("/sites", response_class=HTMLResponse)
def sites_page(request: Request):
    tenant = _tenant(request)
    s, _ = _site_queries(tenant["id"])
    return templates.TemplateResponse(
        request, "sites.html", context=ctx(request, page="sites", sites=s))


@app.post("/sites")
def add_site(request: Request, domain: str = Form(...)):
    tenant = _tenant(request)
    domain = domain.strip().lower().replace("https://", "").replace("http://", "").rstrip("/")
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO sites (tenant_id, domain) VALUES (?, ?)",
                (tenant["id"], domain))
            site_id = cur.lastrowid
        except Exception:
            # Site already exists
            row = q1(conn, "SELECT id FROM sites WHERE tenant_id = ? AND domain = ?",
                     (tenant["id"], domain))
            site_id = row["id"] if row else 0

    if site_id:
        _run_crawl(site_id, domain)
    return RedirectResponse(f"/sites?lang={_resolve_lang(request)}", 303)


@app.get("/sites/{site_id}", response_class=HTMLResponse)
def site_detail(request: Request, site_id: int, page: int = 1):
    tenant = _tenant(request)
    sites, pages_by_site = _site_queries(tenant["id"])
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        return RedirectResponse("/sites", 303)
    all_pages = pages_by_site.get(site_id, [])
    per_page = 20
    total_pages = max(1, (len(all_pages) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    paged = all_pages[(page - 1) * per_page : page * per_page]
    return templates.TemplateResponse(
        request, "site.html",
        context=ctx(request, page="sites", site=site, pages=paged,
                    page_num=page, total_pages=total_pages, total_pages_count=len(all_pages)))


@app.post("/sites/{site_id}/crawl")
def crawl_site(request: Request, site_id: int):
    tenant = _tenant(request)
    with connect() as conn:
        row = q1(conn, "SELECT * FROM sites WHERE id = ? AND tenant_id = ?", (site_id, tenant["id"]))
    if row:
        site = dict(row)
        _run_crawl(site_id, site["domain"])
    return RedirectResponse(f"/sites/{site_id}?lang={_resolve_lang(request)}", 303)


@app.post("/sites/{site_id}/cancel")
def cancel_crawl(request: Request, site_id: int):
    tenant = _tenant(request)
    with connect() as conn:
        conn.execute(
            "UPDATE sites SET status = 'pending', crawl_error = 'Cancelled by user', crawl_progress = '' WHERE id = ? AND tenant_id = ? AND status = 'crawling'",
            (site_id, tenant["id"]))
    back = request.headers.get("referer", "/sites")
    return RedirectResponse(back, 303)


@app.post("/sites/{site_id}/delete")
def delete_site(request: Request, site_id: int):
    tenant = _tenant(request)
    with connect() as conn:
        conn.execute("DELETE FROM sites WHERE id = ? AND tenant_id = ?", (site_id, tenant["id"]))
    return RedirectResponse(f"/sites?lang={_resolve_lang(request)}", 303)


@app.get("/sites/{site_id}/pages/{page_id}", response_class=HTMLResponse)
def page_detail(request: Request, site_id: int, page_id: int):
    tenant = _tenant(request)
    with connect() as conn:
        site = q1(conn, "SELECT * FROM sites WHERE id = ? AND tenant_id = ?", (site_id, tenant["id"]))
        if not site:
            return RedirectResponse("/sites", 303)
        page_row = q1(conn, "SELECT * FROM pages WHERE id = ? AND site_id = ?", (page_id, site_id))
        if not page_row:
            return RedirectResponse(f"/sites/{site_id}", 303)
    return templates.TemplateResponse(
        request, "page_detail.html",
        context=ctx(request, page="sites", site=dict(site), page_data=dict(page_row)))


@app.get("/sites/{site_id}/generate", response_class=HTMLResponse)
def generate_page(request: Request, site_id: int, page: int = 1):
    tenant = _tenant(request)
    sites, pages_by_site = _site_queries(tenant["id"])
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site or site["status"] != "ready":
        return RedirectResponse(f"/sites/{site_id}", 303)
    all_pages = pages_by_site.get(site_id, [])
    per_page = 30
    total_pages = max(1, (len(all_pages) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    paged = all_pages[(page - 1) * per_page : page * per_page]
    return templates.TemplateResponse(
        request, "generate.html",
        context=ctx(request, page="sites", site=site, pages=paged,
                    page_num=page, total_pages=total_pages, total_items=len(all_pages)))


@app.post("/api/generate")
async def api_generate(request: Request):
    tenant = _tenant(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    site_id = int(body.get("site_id", 0))
    prompt = str(body.get("prompt", ""))
    fmt = str(body.get("format", "linkedin_post"))

    with connect() as conn:
        site = q1(conn, "SELECT * FROM sites WHERE id = ? AND tenant_id = ?", (site_id, tenant["id"]))
        if not site:
            return JSONResponse({"error": "Site not found"}, 404)
        chunks_list = rag.retrieve(conn, site_id, prompt)

    if not chunks_list:
        return JSONResponse({"error": "No relevant content found. Try re-crawling the site."}, 400)

    try:
        system_prompt = rag.build_prompt(chunks_list, prompt, fmt)
        content = rag.generate_with_llm(system_prompt)
    except Exception as e:
        return JSONResponse({"error": f"Generation failed: {e}"}, 500)

    return JSONResponse({"content": content, "sources": len(chunks_list)})


@app.post("/api/drafts")
def create_draft_endpoint(request: Request, site_id: int = Form(...), prompt: str = Form(...),
                          format: str = Form("linkedin_post"), content: str = Form(...)):
    tenant = _tenant(request)
    draft_id = drafts.create_draft(tenant["id"], site_id, prompt, format, content)
    return RedirectResponse(f"/drafts/{draft_id}?lang={_resolve_lang(request)}", 303)


@app.get("/drafts", response_class=HTMLResponse)
def drafts_page(request: Request, status: str = "all"):
    tenant = _tenant(request)
    status_filter = status if status in ("draft", "published") else None
    drafts_list = drafts.list_drafts(tenant["id"], status_filter)
    return templates.TemplateResponse(
        request, "drafts.html",
        context=ctx(request, page="drafts", drafts_list=drafts_list, current_status=status))


@app.get("/drafts/{draft_id}", response_class=HTMLResponse)
def draft_view(request: Request, draft_id: int):
    tenant = _tenant(request)
    d = drafts.get_draft(tenant["id"], draft_id)
    if not d:
        return RedirectResponse("/drafts", 303)
    return templates.TemplateResponse(
        request, "draft.html", context=ctx(request, page="drafts", draft=d))


@app.post("/drafts/{draft_id}")
def draft_action(request: Request, draft_id: int, action: str = Form("save"),
                 content: str = Form(None)):
    tenant = _tenant(request)
    if action == "save" and content is not None:
        drafts.update_draft(tenant["id"], draft_id, content=content)
    elif action == "publish":
        d = drafts.get_draft(tenant["id"], draft_id)
        new_status = "draft" if (d and d["status"] == "published") else "published"
        drafts.update_draft(tenant["id"], draft_id, status=new_status)
    return RedirectResponse(f"/drafts/{draft_id}?lang={_resolve_lang(request)}", 303)


@app.post("/drafts/{draft_id}/delete")
def draft_delete(request: Request, draft_id: int):
    tenant = _tenant(request)
    drafts.delete_draft(tenant["id"], draft_id)
    return RedirectResponse(f"/drafts?lang={_resolve_lang(request)}", 303)


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    tenant = _tenant(request)
    with connect() as conn:
        own = workspace.own_brand(conn, tenant["id"])
        has_data = own is not None
        sites = conn.execute(
            "SELECT * FROM sites WHERE tenant_id = ? AND status = 'ready'",
            (tenant["id"],),
        ).fetchall()
        has_sites = len(sites) > 0
    return templates.TemplateResponse(
        request, "reports.html",
        context=ctx(request, page="reports", has_data=has_data, brand=own,
                    has_sites=has_sites))


@app.get("/reports/download")
def reports_download(request: Request, days: int = 30, format: str = "md"):
    tenant = _tenant(request)
    lang = _resolve_lang(request)
    with connect() as conn:
        own = workspace.own_brand(conn, tenant["id"])
    brand_slug = (own["name"] if own else "brand").lower().replace(" ", "-")
    if format == "pdf":
        pdf = report.generate_visibility_pdf(tenant["id"], days, lang=lang)
        if pdf is None:
            return RedirectResponse("/reports", 303)
        from fastapi.responses import Response
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition":
                                 f'attachment; filename="geo-visibility-{brand_slug}-{days}d.pdf"'})
    md = report.generate_markdown(tenant["id"], days)
    if md is None:
        return RedirectResponse("/reports", 303)
    filename = f"geo-visibility-{brand_slug}-{days}d.md"
    from fastapi.responses import Response
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Audit progress tracking (in-memory) ──────────────────────────────────

_audit_jobs: dict[str, dict] = {}


def _audit_worker(job_id: str, tenant_id: int, days: int):
    """Run audit generation in background, updating _audit_jobs with dummy progress."""
    now = time.time()
    try:
        _audit_jobs[job_id] = {"progress": 5, "label": "正在讀取網站數據…", "done": False, "_ts": now}

        data = audit_report.build_audit_data(tenant_id, days)
        if data is None:
            _audit_jobs[job_id] = {"progress": 0, "label": "錯誤", "done": True, "error": "無品牌數據", "_ts": now}
            return

        api_key, base_url, model = audit_report._get_llm()
        if not api_key:
            _audit_jobs[job_id] = {"progress": 0, "label": "錯誤", "done": True, "error": "未設定 LLM API 金鑰", "_ts": now}
            return

        # Run the analysis (single LLM call in the d0e0416 version)
        _audit_jobs[job_id] = {"progress": 20, "label": "分析網站內容與 AI 可見度…", "_ts": now}
        data = audit_report.analyze(tenant_id, days)
        if data is None or data.get("error"):
            _audit_jobs[job_id] = {"progress": 0, "label": "錯誤", "done": True, "error": "分析失敗", "_ts": now}
            return

        _audit_jobs[job_id] = {"progress": 80, "label": "生成 PDF 報告…", "_ts": now}
        from weasyprint import HTML
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(BASE / "templates")))
        template = env.get_template("audit_report.html")
        html_str = template.render(**data)
        pdf_bytes = HTML(string=html_str).write_pdf()

        _audit_jobs[job_id] = {"progress": 100, "label": "完成", "done": True, "pdf": pdf_bytes, "_ts": time.time()}
    except Exception as e:
        _audit_jobs[job_id] = {"progress": 0, "label": "錯誤", "done": True, "error": str(e)[:300], "_ts": time.time()}

    # Cleanup old jobs (>10 min)
    now2 = time.time()
    stale = [k for k, v in _audit_jobs.items() if v.get("done") and v.get("_ts", 0) > 0 and v.get("_ts", 0) < now2 - 600]
    for k in stale:
        _audit_jobs.pop(k, None)


@app.get("/reports/audit")
def reports_audit_download(request: Request, days: int = 30):
    """Generate and download a comprehensive GEO audit report as PDF."""
    tenant = _tenant(request)
    pdf_bytes = audit_report.generate_pdf(tenant["id"], days)
    if pdf_bytes is None:
        return RedirectResponse("/reports", 303)
    with connect() as conn:
        own = workspace.own_brand(conn, tenant["id"])
    brand_slug = (own["name"] if own else "brand").lower().replace(" ", "-")
    filename = f"geo-audit-{brand_slug}-{days}d.pdf"
    return Response(pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/reports/audit/loading")
def reports_audit_loading(request: Request):
    """Show a loading spinner while the audit generates, then trigger download."""
    return templates.TemplateResponse(request, "loading.html", context=ctx(request, page="reports"))


@app.post("/api/audit/start")
def api_audit_start(request: Request, days: int = 30):
    """Start background audit generation, return job ID."""
    tenant = _tenant(request)
    job_id = uuid.uuid4().hex[:12]
    _audit_jobs[job_id] = {"progress": 0, "label": "準備中…", "done": False, "_ts": time.time()}
    t = Thread(target=_audit_worker, args=(job_id, tenant["id"], days), daemon=True)
    t.start()
    return JSONResponse({"job_id": job_id})


@app.get("/api/audit/progress/{job_id}")
def api_audit_progress(job_id: str):
    """Poll for audit generation progress."""
    job = _audit_jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "job not found"}, 404)
    return JSONResponse({
        "progress": job["progress"],
        "label": job["label"],
        "done": job.get("done", False),
        "error": job.get("error"),
    })


@app.get("/reports/audit/result/{job_id}")
def reports_audit_result(request: Request, job_id: str):
    """Download completed audit PDF."""
    job = _audit_jobs.get(job_id)
    if job is None or not job.get("pdf"):
        return RedirectResponse("/reports", 303)
    tenant = _tenant(request)
    with connect() as conn:
        own = workspace.own_brand(conn, tenant["id"])
    brand_slug = (own["name"] if own else "brand").lower().replace(" ", "-")
    filename = f"geo-audit-{brand_slug}.pdf"
    return Response(job["pdf"], media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _run_crawl(site_id: int, domain: str):
    """Run crawl synchronously (in background via FastAPI thread pool)."""
    with connect() as conn:
        conn.execute("UPDATE sites SET status = 'crawling', crawl_error = NULL, crawl_progress = '' WHERE id = ?", (site_id,))

    import threading

    def _do():
        try:
            def _cancelled() -> bool:
                with connect() as c:
                    r = c.execute("SELECT status FROM sites WHERE id = ?", (site_id,)).fetchone()
                return not r or r["status"] != "crawling"

            with connect() as conn:
                conn.execute("UPDATE sites SET crawl_progress = 'Discovering pages from sitemap…' WHERE id = ?", (site_id,))
            urls = crawler.discover(domain)
            if _cancelled():
                return
            print(f"[crawl:{site_id}] discovered {len(urls)} URLs for {domain}")

            pages_data: list[dict] = []
            seen_urls: set[str] = set()
            for i, url in enumerate(urls):
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                if _cancelled():
                    return
                with connect() as conn:
                    conn.execute("UPDATE sites SET crawl_progress = ? WHERE id = ?",
                                 (f"Fetching page {i+1}/{len(urls)}…", site_id))
                page = crawler.fetch_page(url)
                if page is not None:
                    pages_data.append(page)
            print(f"[crawl:{site_id}] fetched {len(pages_data)} pages")

            if pages_data:
                if _cancelled():
                    return
                with connect() as conn:
                    conn.execute("UPDATE sites SET crawl_progress = 'Saving pages…' WHERE id = ?", (site_id,))
                    conn.execute("DELETE FROM pages WHERE site_id = ?", (site_id,))
                    for page in pages_data:
                        cur = conn.execute(
                            "INSERT INTO pages (site_id, url, path, title, headings, content) VALUES (?, ?, ?, ?, ?, ?)",
                            (site_id, page["url"], page["path"], page["title"], page["headings"], page["content"]),
                        )
                        page_id = cur.lastrowid
                        for i, ch in enumerate(chunk.chunk_text(page["content"])):
                            conn.execute(
                                "INSERT INTO chunks (page_id, site_id, seq, content) VALUES (?, ?, ?, ?)",
                                (page_id, site_id, i, ch),
                            )

            # Compute embeddings in batches (faster than per-chunk)
            with connect() as conn:
                rows = conn.execute(
                    "SELECT id, content FROM chunks WHERE site_id = ? AND embedding = ''",
                    (site_id,),
                ).fetchall()
            if rows and not _cancelled():
                with connect() as conn:
                    conn.execute("UPDATE sites SET crawl_progress = ? WHERE id = ?",
                                 (f"Computing embeddings for {len(rows)} chunks…", site_id))
                contents = [r["content"] for r in rows]
                vecs = embeddings.embed(contents)
                if _cancelled():
                    return
                for r, vec in zip(rows, vecs):
                    with connect() as conn:
                        conn.execute(
                            "UPDATE chunks SET embedding = ? WHERE id = ?",
                            (embeddings.pack(vec), r["id"]),
                        )

            with connect() as conn:
                if not pages_data:
                    conn.execute(
                        "UPDATE sites SET status = 'failed', crawl_error = 'No pages could be fetched (site may block crawlers)', crawl_progress = '', page_count = 0, last_crawled = datetime('now') WHERE id = ?",
                        (site_id,))
                else:
                    conn.execute(
                        "UPDATE sites SET status = 'ready', crawl_progress = '', page_count = ?, last_crawled = datetime('now') WHERE id = ?",
                        (len(pages_data), site_id),
                    )
        except Exception as e:
            print(f"[crawl:{site_id}] FAILED: {e}", flush=True)
            with connect() as conn:
                conn.execute(
                    "UPDATE sites SET status = 'failed', crawl_error = ?, crawl_progress = '' WHERE id = ?",
                    (str(e)[:500], site_id),
                )

    threading.Thread(target=_do, daemon=True).start()
