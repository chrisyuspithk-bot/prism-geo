"""Website crawler: sitemap → page list → text extraction."""

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx

_MAX_PAGES = 12
_HEADERS = {"User-Agent": "prism-geo/1.0 (content-crawler)"}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def discover(domain: str, timeout: int = 15) -> list[str]:
    """Return a list of URLs to crawl for a domain (sitemap-first, then homepage)."""
    base = f"https://{domain}"
    urls = _from_sitemap(base, timeout)
    if not urls:
        urls = [base]
    return urls[:_MAX_PAGES]


def _from_sitemap(base: str, timeout: int) -> list[str]:
    """Try /sitemap.xml and /sitemap_index.xml, return discovered URLs."""
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        try:
            resp = httpx.get(base + path, headers=_HEADERS, timeout=timeout, follow_redirects=True)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.text)
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            urls = []
            for loc in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
                urls.append(loc.text.strip())
            if urls:
                return urls
        except Exception:
            continue
    return []


def fetch_page(url: str, timeout: int = 15) -> dict | None:
    """Fetch a page and extract title, headings, and clean text."""
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = _clean(m.group(1))

    headings: list[str] = []
    for m in re.finditer(r"<h[12][^>]*>(.*?)</h[12]>", html, re.I | re.S):
        h = _clean(m.group(1))
        if h and len(h) > 3:
            headings.append(h)

    # Extract main content: prefer <main>, <article>, or body
    body = html
    for tag in ("main", "article"):
        m = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", html, re.I | re.S)
        if m:
            body = m.group(1)
            break

    # Strip scripts, styles, nav, footer
    for tag in ("script", "style", "nav", "footer", "header", "noscript"):
        body = re.sub(f"<{tag}[^>]*>.*?</{tag}>", "", body, flags=re.I | re.S)

    text = _clean(body)
    if len(text) < 100:
        return None

    parsed = urlparse(url)
    return {
        "url": url,
        "path": parsed.path or "/",
        "title": title,
        "headings": "\n".join(headings),
        "content": text,
    }


def _clean(html: str) -> str:
    """Strip tags, decode entities, collapse whitespace."""
    text = _TAG_RE.sub(" ", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return _WS_RE.sub(" ", text).strip()
