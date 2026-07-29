"""prism — multi-tenant AI visibility tracking (GEO/AEO). FastAPI web app.

prism is the GEO provider: one deployment hosts many clients. Each client
(tenant) has its own domain, competitors, and prompt set; every dashboard and
evaluation is scoped to the tenant selected in the sidebar. Engine API keys are
configured once, centrally, by the operator and shared across all tenants.
"""

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import i18n, jobs, keystore, queries, scheduler, workspace
from .db import connect, init_db, q, q1
from .onboarding import analyze_website, generate_prompts

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="prism", description="AI visibility tracking (GEO/AEO)")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.on_event("startup")
async def startup() -> None:
    init_db()
    jobs.recover_stale_jobs()
    jobs.ensure_worker()
    scheduler.ensure_scheduler()


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
    return {"request": request, "models": models_list, "tenant": tenant,
            "PRISM_KEY": keystore.has_any_key(), "lang": lang,
            "languages": i18n.LANGUAGES,
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
    with connect() as conn:
        data = queries.citations_page(conn, tenant["id"], days, model_id)
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
    with connect() as conn:
        data = queries.prompt_detail(conn, prompt_id, days)
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
    return templates.TemplateResponse(
        request, "settings_keys.html",
        context=ctx(request, page="settings-keys", providers=keystore.provider_status(),
                    schedule_enabled=(enabled["value"] == "1") if enabled else True,
                    schedule_hour=int(hour["value"]) if hour else 2))


@app.post("/settings/keys")
def save_keys(request: Request, provider: str = Form(...),
              api_key: str = Form(""), base_url: str = Form(""),
              model: str = Form(""), enabled: str = Form("0")):
    if provider in keystore.PROVIDERS:
        if api_key.strip():
            keystore.set_value(f"{provider}_api_key", api_key.strip())
        if base_url.strip():
            keystore.set_value(f"{provider}_base_url", base_url.strip())
        if model.strip():
            keystore.set_value(f"{provider}_model", model.strip())
        keystore.set_value(f"{provider}_enabled", "1" if enabled == "1" else "0")
    return RedirectResponse("/settings/keys?saved=1", status_code=303)


@app.post("/settings/keys/{provider}/clear")
def clear_key(provider: str):
    if provider in keystore.PROVIDERS:
        for suffix in ("api_key", "base_url", "model", "enabled"):
            keystore.set_value(f"{provider}_{suffix}", "")
    return RedirectResponse("/settings/keys", status_code=303)


@app.post("/settings/schedule")
def save_schedule(enabled: str = Form("0"), hour: str = Form("2")):
    keystore.set_value("schedule_enabled", "1" if enabled == "1" else "0")
    keystore.set_value("schedule_hour", hour)
    return RedirectResponse("/settings/keys?saved=1", status_code=303)


@app.post("/admin/reset-db")
def reset_db():
    with connect() as conn:
        for table in ("mentions", "citations", "runs", "models",
                       "competitors", "brands", "prompts", "jobs",
                       "settings", "tenants"):
            conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
    return RedirectResponse("/", status_code=303)


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
