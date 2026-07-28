"""Extract brand mentions and citations from an AI answer's text.

Pure functions, no IO — unit-testable in isolation. This is the GEO analysis
core: given a raw answer-engine response, figure out which brands were
mentioned (and in what order) and which source URLs the answer grounded on.
"""

import re
from urllib.parse import urlparse

# Brand aliases: lowercase alias -> canonical brand name. In a multi-tenant app
# this would live in the DB per brand; here it's a module constant configured
# for the demo workspace plus sensible defaults.
DEFAULT_ALIASES: dict[str, str] = {
    "nike": "Nike",
    "adidas": "Adidas",
    "asics": "Asics",
    "new balance": "New Balance",
    "nb": "New Balance",
    "under armour": "Under Armour",
    "hoka": "Hoka",
    "hoka one one": "Hoka",
    "saucony": "Saucony",
    "brooks": "Brooks",
    "puma": "Puma",
    "reebok": "Reebok",
    "altra": "Altra",
    "salomon": "Salomon",
    "vans": "Vans",
    "mizuno": "Mizuno",
    "allbirds": "Allbirds",
    "on running": "On",
    "on cloud": "On",
    "on": "On",
}

URL_RE = re.compile(r"https?://[^\s)\]>\"']+")

# Domain category heuristics for citation classification. Order matters:
# first matching rule wins.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("social", ("reddit.com", "twitter.com", "x.com", "instagram.com",
                "facebook.com", "tiktok.com", "youtube.com", "quora.com",
                "linkedin.com", "pinterest.com")),
    ("reference", ("wikipedia.org", "britannica.com")),
    ("reviews", ("wirecutter.com", "nytimes.com/wirecutter", "rtings.com",
                 "tomsguide.com", "techradar.com", "cnet.com", "pcmag.com",
                 "runrepeat.com", "believeintherun.com", "roadtrailrun.com",
                 "doctorsreview.net", "verywellfit.com")),
    ("editorial", ("runnersworld.com", "outsideonline.com", "si.com",
                   "espn.com", "forbes.com", "businessinsider.com",
                   "travelandleisure.com", "gq.com", "menshealth.com",
                   "womenshealthmag.com", "shape.com", "self.com",
                   "nytimes.com", "theguardian.com", "wsj.com",
                   "bloomberg.com", "vogue.com", "complex.com",
                   "highsnobiety.com", "hypebeast.com", "footwearnews.com")),
    ("institutional", (".gov", ".edu", "ncbi.nlm.nih.gov", "pubmed")),
    ("ecommerce", ("amazon.com", "zappos.com", "dickssportinggoods.com",
                   "footlocker.com", "finishline.com", "stockx.com",
                   "goat.com", "farfetch.com", "ssense.com", "nordstrom.com",
                   "ubuy.", "lennyshoe.com", "rei.com", "walmart.com",
                   "target.com", "ebay.com", "dsw.com", "jdsports.com")),
]


def normalize_domain(url: str) -> str:
    """Lowercase host without leading www."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def categorize(domain: str, brand_domains: set[str] | None = None) -> str:
    """Classify a cited domain into brand / social / reviews / editorial / ..."""
    if brand_domains and any(domain == d or domain.endswith("." + d) for d in brand_domains):
        return "brand"
    for category, needles in CATEGORY_RULES:
        for needle in needles:
            if needle.startswith("."):
                if domain.endswith(needle):
                    return category
            elif needle in domain:
                return category
    return "other"


def find_mentions(text: str, aliases: dict[str, str] | None = None) -> list[dict]:
    """Find brand mentions in response text.

    Returns one entry per brand: {brand, position, count}, where position is
    the 1-based rank of the brand's first occurrence among all brands found
    (useful for 'mentioned first' analyses).
    """
    aliases = aliases or DEFAULT_ALIASES
    lowered = text.lower()
    hits: dict[str, dict] = {}
    for alias, canonical in aliases.items():
        # word-boundary match so "on" doesn't match "motion"
        matches = list(re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered))
        if not matches:
            continue
        first = matches[0].start()
        entry = hits.setdefault(canonical, {"brand": canonical, "first": first, "count": 0})
        entry["first"] = min(entry["first"], first)
        entry["count"] += len(matches)

    ordered = sorted(hits.values(), key=lambda h: h["first"])
    return [
        {"brand": h["brand"], "position": i + 1, "count": h["count"]}
        for i, h in enumerate(ordered)
    ]


def find_citations(text: str, brand_domains: set[str] | None = None) -> list[dict]:
    """Extract cited URLs from response text, deduped, with domain + category.

    Dedup keys on a canonicalized URL (lowercase, no www) so the same page
    cited with different host spellings counts once.
    """
    seen: dict[str, dict] = {}
    for url in URL_RE.findall(text):
        url = url.rstrip(".,;:")
        domain = normalize_domain(url)
        if not domain:
            continue
        key = url.lower().replace("://www.", "://", 1)
        if key not in seen:
            seen[key] = {
                "url": key,
                "domain": domain,
                "category": categorize(domain, brand_domains),
            }
    return list(seen.values())


def domain_of(url: str) -> str:
    """Root domain (no www) of a URL or bare domain string."""
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host[4:] if host.startswith("www.") else host
