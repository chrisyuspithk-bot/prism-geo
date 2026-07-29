"""PDF report generation for weekly/monthly GEO visibility digests."""

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from . import i18n, queries
from .db import connect

_ENV = Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parent / "templates")))
PERIODS = {"weekly": (7, "每週", "Weekly"), "monthly": (30, "每月", "Monthly")}

_PERIOD_KEY = {"weekly": "weekly", "monthly": "monthly"}


def generate(tenant_id: int, tenant_name: str, brand_name: str,
             period: str, model_id: int | None = None, lang: str = "en") -> bytes:
    days, _, _ = PERIODS.get(period, PERIODS["monthly"])
    with connect() as conn:
        data = _build_data(conn, tenant_id, days, model_id)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    period_key = _PERIOD_KEY.get(period, "monthly")
    period_label = i18n.t(lang, period_key)

    model_name = data["model_name"]
    if model_name == "All models":
        model_name = i18n.t(lang, "all_models")

    def t(key, **fmt):
        return i18n.t(lang, key, **fmt)

    html_str = _ENV.get_template("report.html").render(
        lang=lang,
        t=t,
        generated_at=now,
        brand_name=brand_name,
        period_label=period_label,
        data={**data, "model_name": model_name},
    )
    return HTML(string=html_str).write_pdf()


def _build_data(conn, tenant_id: int, days: int, model_id: int | None) -> dict:
    vis = queries.visibility_page(conn, tenant_id, days, model_id)
    sov = queries.share_of_voice_page(conn, tenant_id, days, model_id)
    cit = queries.citations_page(conn, tenant_id, days, model_id)

    mention_count = sum(c["runs"] for c in vis.get("cards", []))
    model_name = "All models"
    if model_id:
        row = conn.execute("SELECT name FROM models WHERE id = ?", (model_id,)).fetchone()
        if row:
            model_name = row["name"]

    return {
        "overall": vis["overall"],
        "prompt_count": vis["prompt_count"],
        "run_count": vis["run_count"],
        "mention_count": mention_count,
        "cards": vis["cards"],
        "sov": sov.get("table", []),
        "citations": cit.get("top_domains", []),
        "model_name": model_name,
    }
