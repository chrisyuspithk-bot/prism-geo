"""Pure statistics for AI-visibility analysis.

These power the Visibility, Share of Voice, Citations and Opportunities pages.
All functions take plain rows/dicts and never touch the database.
"""

from collections import defaultdict


def visibility_by_day(runs: list[dict], brand_name: str) -> list[dict]:
    """Daily visibility % for one brand: % of runs that day mentioning the brand.

    `runs` is a list of {day, mentioned} dicts (mentioned = 0/1).
    """
    by_day: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        by_day[r["day"]].append(1 if r["mentioned"] else 0)
    return [
        {"day": day, "pct": round(100 * sum(v) / len(v), 1)}
        for day, v in sorted(by_day.items())
    ]


def competitor_by_day(runs: list[dict], brand_name: str) -> list[dict]:
    """Daily visibility % for one competitor brand from runs with {day, brands}."""
    by_day: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        by_day[r["day"]].append(1 if brand_name in (r.get("brands") or []) else 0)
    return [
        {"day": day, "pct": round(100 * sum(v) / len(v), 1)}
        for day, v in sorted(by_day.items())
    ]


def overall_visibility(total_runs: int, mentioned_runs: int) -> float:
    if total_runs == 0:
        return 0.0
    return round(100 * mentioned_runs / total_runs, 1)


def share_of_voice(mention_counts: dict[str, int]) -> list[dict]:
    """Brand mention share: brand -> % of all brand mentions.

    `mention_counts` maps brand name -> number of runs mentioning it.
    Returns sorted list of {brand, mentions, share}.
    """
    total = sum(mention_counts.values())
    rows = [
        {
            "brand": b,
            "mentions": m,
            "share": round(100 * m / total, 1) if total else 0.0,
        }
        for b, m in mention_counts.items()
    ]
    rows.sort(key=lambda r: (-r["mentions"], r["brand"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def average_position(positions: list[int]) -> float | None:
    """Average first-mention rank (1 = mentioned first in the answer)."""
    if not positions:
        return None
    return round(sum(positions) / len(positions), 1)


def stability_score(daily_domain_counts: list[dict]) -> int | None:
    """Citation stability 0-100: how much the cited-domain mix churns day to day.

    Uses Bray-Curtis distance on daily citation-share vectors (same idea as
    Elmo's stability score): 100 = the sources carrying the answers never move.
    None if fewer than two days of data.
    """
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in daily_domain_counts:
        by_day[r["day"]][r["domain"]] += r["count"]
    days = [d for d in sorted(by_day) if by_day[d]]
    if len(days) < 2:
        return None

    total_dist = 0.0
    transitions = 0
    for prev_day, cur_day in zip(days, days[1:]):
        prev, cur = by_day[prev_day], by_day[cur_day]
        prev_total, cur_total = sum(prev.values()), sum(cur.values())
        overlap = sum(
            min(cur[d] / cur_total, prev[d] / prev_total)
            for d in cur
            if d in prev
        )
        total_dist += 1 - overlap
        transitions += 1
    return round((1 - total_dist / transitions) * 100)


def prompt_opportunity_score(
    own_visibility: float,
    competitor_mentions: int,
    total_runs: int,
) -> float:
    """0-100 opportunity score for a prompt where competitors show but you don't.

    Higher when the prompt runs often and competitors are consistently present.
    """
    if own_visibility >= 100 or competitor_mentions == 0:
        return 0.0
    competitor_rate = competitor_mentions / total_runs if total_runs else 0
    gap = (100 - own_visibility) / 100
    return round(100 * gap * competitor_rate, 1)
