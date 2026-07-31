"""Read layer: SQL aggregations backing each dashboard page.

Every function takes a connection plus optional filters (days lookback,
model id) and returns plain dicts ready for templates or the JSON API.
"""

from . import stats
from .db import q, q1


def _model_filter(model_id: int | None) -> tuple[str, tuple]:
    if model_id:
        return " AND r.model_id = ?", (model_id,)
    return "", ()


def _dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def overview(conn, tenant_id: int, days: int = 30, model_id: int | None = None) -> dict:
    mf, mp = _model_filter(model_id)
    own = q1(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1", (tenant_id,))
    if own is None:
        last = q1(conn, "SELECT MAX(ran_at) AS t FROM runs WHERE tenant_id = ?", (tenant_id,))
        return {"brand": None, "prompts": 0, "runs": 0, "citations": 0,
                "visibility": 0.0, "last_run": last["t"] if last else None,
                "runs_by_engine": []}
    totals = q1(
        conn,
        f"""SELECT COUNT(*) AS runs, COUNT(DISTINCT r.prompt_id) AS prompts
            FROM runs r WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}""",
        (tenant_id, f"-{days} days", *mp),
    )
    cited = q1(
        conn,
        f"""SELECT COUNT(*) AS n FROM citations c JOIN runs r ON r.id = c.run_id
            WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}""",
        (tenant_id, f"-{days} days", *mp),
    )
    vis = q1(
        conn,
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN m.id IS NOT NULL THEN 1 ELSE 0 END) AS mentioned
            FROM runs r
            LEFT JOIN mentions m ON m.run_id = r.id AND m.brand_id = ?
            WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) AND r.status = 'ok' {mf}""",
        (own["id"], tenant_id, f"-{days} days", *mp),
    )
    last = q1(conn, "SELECT MAX(ran_at) AS t FROM runs WHERE tenant_id = ?", (tenant_id,))
    runs_by_engine = q(
        conn,
        f"""SELECT m.name, COUNT(*) AS n
            FROM runs r JOIN models m ON m.id = r.model_id
            WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY m.name ORDER BY n DESC""",
        (tenant_id, f"-{days} days", *mp),
    )
    return {
        "brand": dict(own),
        "prompts": totals["prompts"] or 0,
        "runs": totals["runs"] or 0,
        "citations": cited["n"] or 0,
        "visibility": stats.overall_visibility(vis["total"] or 0, vis["mentioned"] or 0),
        "last_run": last["t"],
        "runs_by_engine": _dicts(runs_by_engine),
    }


def visibility_page(conn, tenant_id: int, days: int = 30, model_id: int | None = None) -> dict:
    """Per-prompt visibility with brand chips and daily trend series."""
    mf, mp = _model_filter(model_id)
    own = q1(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1", (tenant_id,))
    if own is None:
        return {"cards": [], "overall": 0.0, "prompt_count": 0, "run_count": 0}
    prompts = q(conn, "SELECT * FROM prompts WHERE active = 1 AND tenant_id = ? ORDER BY id",
                (tenant_id,))

    # All successful runs in window with their mention set, in one pass.
    runs = q(
        conn,
        f"""SELECT r.id, r.prompt_id, date(r.ran_at, '+8 hours') AS day,
                   GROUP_CONCAT(b.name, CHAR(10)) AS brands
            FROM runs r
            LEFT JOIN mentions m ON m.run_id = r.id
            LEFT JOIN brands b ON b.id = m.brand_id
            WHERE r.tenant_id = ? AND r.status = 'ok' AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY r.id""",
        (tenant_id, f"-{days} days", *mp),
    )

    per_prompt: dict[int, dict] = {
        p["id"]: {"prompt": dict(p), "runs": 0, "mentioned": 0, "brands": {}, "series": []}
        for p in prompts
    }
    series_rows: dict[int, list[dict]] = {p["id"]: [] for p in prompts}
    for r in runs:
        slot = per_prompt.get(r["prompt_id"])
        if slot is None:
            continue
        brands = [b for b in (r["brands"] or "").split("\n") if b]
        mentioned = own["name"] in brands
        slot["runs"] += 1
        slot["mentioned"] += 1 if mentioned else 0
        for b in brands:
            slot["brands"][b] = slot["brands"].get(b, 0) + 1
        series_rows[r["prompt_id"]].append({"day": r["day"], "mentioned": mentioned,
                                            "brands": brands})

    COMP_COLORS = ["#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4"]

    cards = []
    for pid, slot in per_prompt.items():
        series = stats.visibility_by_day(series_rows[pid], own["name"])
        vis = stats.overall_visibility(slot["runs"], slot["mentioned"])
        top = sorted(slot["brands"].items(), key=lambda kv: -kv[1])[:6]

        # Competitor sparkline data: top 3 competitors by mention count
        comp_names = [n for n, _ in top if n != own["name"]][:3]
        competitors = []
        for i, cn in enumerate(comp_names):
            cseries = stats.competitor_by_day(series_rows[pid], cn)
            competitors.append({
                "name": cn, "color": COMP_COLORS[i % len(COMP_COLORS)],
                "series": cseries,
            })

        cards.append({
            "id": pid,
            "text": slot["prompt"]["text"],
            "tags": slot["prompt"]["tags"],
            "visibility": vis,
            "runs": slot["runs"],
            "top_brands": [{"name": n, "count": c} for n, c in top],
            "series": series,
            "competitors": competitors,
        })
    cards.sort(key=lambda c: (-c["visibility"], c["text"]))

    total_runs = sum(c["runs"] for c in cards)
    weighted = sum(c["visibility"] * c["runs"] for c in cards)
    overall = round(weighted / total_runs, 1) if total_runs else 0.0
    return {"cards": cards, "overall": overall, "prompt_count": len(cards),
            "run_count": total_runs, "own": own["name"]}


def share_of_voice_page(conn, tenant_id: int, days: int = 30, model_id: int | None = None) -> dict:
    mf, mp = _model_filter(model_id)
    own = q1(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1", (tenant_id,))
    if own is None:
        return {"table": [], "trend": {}, "days": []}
    rows = q(
        conn,
        f"""SELECT b.name, COUNT(DISTINCT m.run_id) AS runs,
                   AVG(m.position) AS avg_pos,
                   COUNT(DISTINCT r.prompt_id) AS prompts
            FROM mentions m
            JOIN brands b ON b.id = m.brand_id
            JOIN runs r ON r.id = m.run_id
            WHERE r.tenant_id = ? AND r.status = 'ok' AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY b.id""",
        (tenant_id, f"-{days} days", *mp),
    )
    table = stats.share_of_voice({r["name"]: r["runs"] for r in rows})
    meta = {r["name"]: r for r in rows}
    for row in table:
        m = meta[row["brand"]]
        row["avg_position"] = round(m["avg_pos"], 1) if m["avg_pos"] else None
        row["prompts"] = m["prompts"]
        row["is_own"] = row["brand"] == own["name"]

    # Daily SoV trend for top 5 brands.
    daily = q(
        conn,
        f"""SELECT date(r.ran_at, '+8 hours') AS day, b.name, COUNT(DISTINCT m.run_id) AS n
            FROM mentions m JOIN brands b ON b.id = m.brand_id
            JOIN runs r ON r.id = m.run_id
            WHERE r.tenant_id = ? AND r.status = 'ok' AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY day, b.id""",
        (tenant_id, f"-{days} days", *mp),
    )
    days_sorted = sorted({d["day"] for d in daily})
    top5 = [r["brand"] for r in table[:5]]
    per_day_brand: dict[tuple, int] = {(d["day"], d["name"]): d["n"] for d in daily}
    day_totals = {day: sum(v for (dy, _), v in per_day_brand.items() if dy == day)
                  for day in days_sorted}
    trend = {
        b: [
            round(100 * per_day_brand.get((day, b), 0) / day_totals[day], 1)
            if day_totals[day] else 0
            for day in days_sorted
        ]
        for b in top5
    }
    return {"table": table, "trend": trend, "days": days_sorted}


def citations_page(conn, tenant_id: int, days: int = 30, model_id: int | None = None,
                   domain_page: int = 1, url_page: int = 1) -> dict:
    PER = 10
    mf, mp = _model_filter(model_id)
    window = f"-{days} days"

    d_total = q1(conn,
        f"""SELECT COUNT(*) AS n FROM (
              SELECT c.domain FROM citations c JOIN runs r ON r.id = c.run_id
              WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
              GROUP BY c.domain
            )""",
        (tenant_id, window, *mp))["n"]
    d_off = max(0, (domain_page - 1) * PER)
    top_domains = q(
        conn,
        f"""SELECT c.domain, c.category, COUNT(*) AS n,
                   COUNT(DISTINCT c.url) AS urls
            FROM citations c JOIN runs r ON r.id = c.run_id
            WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY c.domain ORDER BY n DESC LIMIT ? OFFSET ?""",
        (tenant_id, window, *mp, PER, d_off),
    )
    d_pages = max(1, (d_total + PER - 1) // PER)

    u_total = q1(conn,
        f"""SELECT COUNT(*) AS n FROM (
              SELECT c.url FROM citations c JOIN runs r ON r.id = c.run_id
              WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
              GROUP BY c.url
            )""",
        (tenant_id, window, *mp))["n"]
    u_off = max(0, (url_page - 1) * PER)
    top_urls = q(
        conn,
        f"""SELECT c.url, c.domain, COUNT(*) AS n
            FROM citations c JOIN runs r ON r.id = c.run_id
            WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY c.url ORDER BY n DESC LIMIT ? OFFSET ?""",
        (tenant_id, window, *mp, PER, u_off),
    )
    u_pages = max(1, (u_total + PER - 1) // PER)

    by_cat = q(
        conn,
        f"""SELECT c.category, COUNT(*) AS n
            FROM citations c JOIN runs r ON r.id = c.run_id
            WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY c.category ORDER BY n DESC""",
        (tenant_id, window, *mp),
    )
    daily = q(
        conn,
        f"""SELECT date(r.ran_at, '+8 hours') AS day, c.domain, COUNT(*) AS count
            FROM citations c JOIN runs r ON r.id = c.run_id
            WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY day, c.domain""",
        (tenant_id, window, *mp),
    )
    return {
        "top_domains": _dicts(top_domains),
        "top_urls": _dicts(top_urls),
        "by_category": _dicts(by_cat),
        "stability": stats.stability_score(daily),
        "total": sum(r["n"] for r in by_cat),
        "domain_page": domain_page, "domain_pages": d_pages, "domain_total": d_total,
        "url_page": url_page, "url_pages": u_pages, "url_total": u_total,
    }


def opportunities_page(conn, tenant_id: int, days: int = 30, model_id: int | None = None) -> dict:
    """Prompts where competitors are visible but the tracked brand is not."""
    mf, mp = _model_filter(model_id)
    own = q1(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1", (tenant_id,))
    if own is None:
        return {"opportunities": []}
    rows = q(
        conn,
        f"""SELECT p.id, p.text, p.tags,
                   COUNT(r.id) AS runs,
                   SUM(CASE WHEN om.id IS NOT NULL THEN 1 ELSE 0 END) AS own_runs,
                   SUM(CASE WHEN cm.brand_id IS NOT NULL THEN 1 ELSE 0 END) AS comp_runs,
                   GROUP_CONCAT(DISTINCT cb.name) AS competitors
            FROM prompts p
            JOIN runs r ON r.prompt_id = p.id AND r.status = 'ok' AND r.tenant_id = ?
            LEFT JOIN mentions om ON om.run_id = r.id AND om.brand_id = ?
            LEFT JOIN mentions cm ON cm.run_id = r.id AND cm.brand_id != ?
            LEFT JOIN brands cb ON cb.id = cm.brand_id
            WHERE p.active = 1 AND p.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) {mf}
            GROUP BY p.id""",
        (tenant_id, own["id"], own["id"], tenant_id, f"-{days} days", *mp),
    )
    opps = []
    for r in rows:
        own_vis = stats.overall_visibility(r["runs"], r["own_runs"] or 0)
        score = stats.prompt_opportunity_score(own_vis, r["comp_runs"] or 0, r["runs"])
        if score <= 0:
            continue
        opps.append({
            "id": r["id"], "text": r["text"], "tags": r["tags"],
            "visibility": own_vis, "score": score, "runs": r["runs"],
            "competitors": sorted({c for c in (r["competitors"] or "").split(",") if c}),
        })
    opps.sort(key=lambda o: -o["score"])
    return {"opportunities": opps}


def prompt_detail(conn, prompt_id: int, days: int = 30,
                  citations_page: int = 1, runs_page: int = 1) -> dict | None:
    CITATIONS_PER = 10
    RUNS_PER = 5

    prompt = q1(conn, "SELECT * FROM prompts WHERE id = ?", (prompt_id,))
    if prompt is None:
        return None
    tenant_id = prompt["tenant_id"]
    own = q1(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1", (tenant_id,))

    # Runs with pagination
    runs_total = q1(conn,
        "SELECT COUNT(*) AS n FROM runs WHERE prompt_id = ?", (prompt_id,))["n"]
    runs_offset = max(0, (runs_page - 1) * RUNS_PER)
    runs = q(
        conn,
        """SELECT r.*, m.name AS model_name FROM runs r
           JOIN models m ON m.id = r.model_id
           WHERE r.prompt_id = ? ORDER BY r.job_id DESC, r.ran_at DESC LIMIT ? OFFSET ?""",
        (prompt_id, RUNS_PER, runs_offset),
    )
    runs_pages = max(1, (runs_total + RUNS_PER - 1) // RUNS_PER)

    # Mentions (no pagination — at most ~10 brands)
    mention_rows = q(
        conn,
        """SELECT b.name, COUNT(DISTINCT m.run_id) AS n, AVG(m.position) AS pos
           FROM mentions m JOIN brands b ON b.id = m.brand_id
           JOIN runs r ON r.id = m.run_id
           WHERE r.prompt_id = ? AND r.status = 'ok' AND r.ran_at >= datetime('now', '+8 hours', ?)
           GROUP BY b.id ORDER BY n DESC""",
        (prompt_id, f"-{days} days"),
    )
    ok_runs = q1(conn,
        "SELECT COUNT(*) AS n FROM runs WHERE prompt_id = ? AND status = 'ok'",
        (prompt_id,))["n"]
    in_window = q1(
        conn,
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN m.id IS NOT NULL THEN 1 ELSE 0 END) AS mentioned
           FROM runs r LEFT JOIN mentions m ON m.run_id = r.id AND m.brand_id = ?
           WHERE r.prompt_id = ? AND r.status = 'ok' AND r.ran_at >= datetime('now', '+8 hours', ?)""",
        (own["id"], prompt_id, f"-{days} days"),
    )

    # Citations with pagination
    cit_total = q1(conn,
        """SELECT COUNT(*) AS n FROM (
             SELECT c.url FROM citations c JOIN runs r ON r.id = c.run_id
             WHERE r.prompt_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?)
             GROUP BY c.url
           )""",
        (prompt_id, f"-{days} days"))["n"]
    cit_offset = max(0, (citations_page - 1) * CITATIONS_PER)
    citations = q(
        conn,
        """SELECT c.url, c.domain, c.category, COUNT(*) AS n
           FROM citations c JOIN runs r ON r.id = c.run_id
           WHERE r.prompt_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?)
           GROUP BY c.url ORDER BY n DESC LIMIT ? OFFSET ?""",
        (prompt_id, f"-{days} days", CITATIONS_PER, cit_offset),
    )
    cit_pages = max(1, (cit_total + CITATIONS_PER - 1) // CITATIONS_PER)

    return {
        "prompt": dict(prompt),
        "runs": _dicts(runs),
        "mention_table": _dicts(mention_rows),
        "citations": _dicts(citations),
        "run_count": ok_runs,
        "visibility": stats.overall_visibility(
            in_window["total"] or 0, in_window["mentioned"] or 0),
        "own_brand": own["name"],
        "runs_page": runs_page,
        "runs_pages": runs_pages,
        "runs_total": runs_total,
        "citations_page": citations_page,
        "citations_pages": cit_pages,
        "citations_total": cit_total,
    }


def models(conn) -> list:
    return q(conn, "SELECT * FROM models ORDER BY name")


def brands(conn, tenant_id: int) -> list:
    return q(conn, "SELECT * FROM brands WHERE tenant_id = ? ORDER BY is_own DESC, name",
             (tenant_id,))


def report_data(conn, tenant_id: int, days: int = 30) -> dict:
    """All data needed for a GEO visibility report."""
    own = q1(conn, "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 1", (tenant_id,))
    if own is None:
        return None
    window = f"-{days} days"
    comps = q(conn,
        "SELECT * FROM brands WHERE tenant_id = ? AND is_own = 0 ORDER BY name",
        (tenant_id,))

    # Visibility overall
    vis = q1(conn,
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN m.id IS NOT NULL THEN 1 ELSE 0 END) AS mentioned
           FROM runs r
           LEFT JOIN mentions m ON m.run_id = r.id AND m.brand_id = ?
           WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?) AND r.status = 'ok'""",
        (own["id"], tenant_id, window))
    visibility = stats.overall_visibility(vis["total"] or 0, vis["mentioned"] or 0)

    # Stats
    totals = q1(conn,
        """SELECT COUNT(*) AS runs, COUNT(DISTINCT r.prompt_id) AS prompts
           FROM runs r WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?)""",
        (tenant_id, window))
    cited = q1(conn,
        """SELECT COUNT(*) AS n FROM citations c JOIN runs r ON r.id = c.run_id
           WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?)""",
        (tenant_id, window))
    last = q1(conn, "SELECT MAX(ran_at) AS t FROM runs WHERE tenant_id = ?", (tenant_id,))

    # Per-prompt visibility
    prompts = q(conn,
        """SELECT p.id, p.text, p.tags,
                  COUNT(r.id) AS runs,
                  SUM(CASE WHEN m.id IS NOT NULL THEN 1 ELSE 0 END) AS mentioned
           FROM prompts p
           LEFT JOIN runs r ON r.prompt_id = p.id AND r.status = 'ok'
             AND r.ran_at >= datetime('now', '+8 hours', ?)
           LEFT JOIN mentions m ON m.run_id = r.id AND m.brand_id = ?
           WHERE p.active = 1 AND p.tenant_id = ?
           GROUP BY p.id ORDER BY p.id""",
        (window, own["id"], tenant_id))
    prompt_rows = []
    for p in prompts:
        pv = stats.overall_visibility(p["runs"] or 0, p["mentioned"] or 0)
        prompt_rows.append({"text": p["text"], "tags": p["tags"], "runs": p["runs"],
                            "visibility": pv})

    # Share of voice
    sov = q(conn,
        """SELECT b.name, COUNT(DISTINCT m.run_id) AS mentions,
                  AVG(m.position) AS avg_pos, COUNT(DISTINCT r.prompt_id) AS prompts
           FROM brands b
           JOIN mentions m ON m.brand_id = b.id
           JOIN runs r ON r.id = m.run_id
           WHERE b.tenant_id = ? AND r.status = 'ok'
             AND r.ran_at >= datetime('now', '+8 hours', ?)
           GROUP BY b.id ORDER BY mentions DESC""",
        (tenant_id, window))
    total_mentions = sum(s["mentions"] for s in sov)

    # Top citations
    top_domains = q(conn,
        """SELECT c.domain, c.category, COUNT(*) AS n
           FROM citations c JOIN runs r ON r.id = c.run_id
           WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?)
           GROUP BY c.domain ORDER BY n DESC LIMIT 15""",
        (tenant_id, window))
    top_urls = q(conn,
        """SELECT c.url, c.domain, COUNT(*) AS n
           FROM citations c JOIN runs r ON r.id = c.run_id
           WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?)
           GROUP BY c.url ORDER BY n DESC LIMIT 15""",
        (tenant_id, window))

    # Engine breakdown
    engines = q(conn,
        """SELECT m.name, COUNT(*) AS n
           FROM runs r JOIN models m ON m.id = r.model_id
           WHERE r.tenant_id = ? AND r.ran_at >= datetime('now', '+8 hours', ?)
           GROUP BY m.name ORDER BY n DESC""",
        (tenant_id, window))

    # Competitor per-prompt gaps (opportunities)
    gaps = []
    for p in prompts:
        if p["runs"] and p["mentioned"] == 0:
            comps_here = q(conn,
                """SELECT DISTINCT b.name FROM mentions m
                   JOIN brands b ON b.id = m.brand_id
                   JOIN runs r ON r.id = m.run_id
                   WHERE r.prompt_id = ? AND r.status = 'ok'
                     AND b.tenant_id = ? AND b.is_own = 0""",
                (p["id"], tenant_id))
            if comps_here:
                gaps.append({"text": p["text"],
                             "competitors": ", ".join(c["name"] for c in comps_here)})

    return {
        "brand": dict(own),
        "competitors": [dict(c) for c in comps],
        "visibility": visibility,
        "runs": totals["runs"] or 0,
        "prompts_count": totals["prompts"] or 0,
        "citations": cited["n"] or 0,
        "last_run": last["t"],
        "days": days,
        "prompt_rows": prompt_rows,
        "all_prompt_rows": prompt_rows,
        "sov": _dicts(sov),
        "total_mentions": total_mentions,
        "top_domains": _dicts(top_domains),
        "top_urls": _dicts(top_urls),
        "engines": _dicts(engines),
        "gaps": gaps,
    }
