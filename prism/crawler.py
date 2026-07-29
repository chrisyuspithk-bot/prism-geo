"""Website crawler: sitemap → page list → text extraction."""

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx

_MAX_PAGES = 0  # 0 = no limit
_HEADERS = {"User-Agent": "prism-geo/1.0 (content-crawler)"}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def discover(domain: str, timeout: int = 15) -> list[str]:
    """Return a list of URLs to crawl for a domain (sitemap-first, then homepage)."""
    base = f"https://{domain}"
    urls = _from_sitemap(base, timeout)
    if not urls:
        urls = [base]
    if _MAX_PAGES > 0:
        urls = urls[:_MAX_PAGES]
    return urls


def _is_sitemap(url: str) -> bool:
    return url.endswith(".xml") and "sitemap" in url.lower()


def _parse_sitemap(url: str, timeout: int, depth: int = 0) -> list[str]:
    """Parse a sitemap XML, following sitemap index references recursively."""
    if depth > 2:
        return []
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
        urls: list[str] = []
        for loc in root.iter(f"{{{ns}}}loc"):
            href = loc.text.strip() if loc.text else ""
            if not href:
                continue
            if _is_sitemap(href):
                urls.extend(_parse_sitemap(href, timeout, depth + 1))
            else:
                urls.append(href)
        return urls
    except Exception:
        return []


def _from_sitemap(base: str, timeout: int) -> list[str]:
    """Try common sitemap paths and parse them."""
    paths = [
        "/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
        "/post-sitemap.xml", "/page-sitemap.xml", "/pages-sitemap.xml",
        "/blog-sitemap.xml", "/blog-posts-sitemap.xml",
        "/wp-sitemap.xml",  # WordPress default
    ]
    for path in paths:
        urls = _parse_sitemap(base + path, timeout)
        if urls:
            return urls
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
    if len(text) < 20:
        # JS-rendered page — fall back to meta/og description, then title
        desc = ""
        for m in re.finditer(
            r'<meta[^>]*(?:name="description"|property="og:description")[^>]*content="([^"]+)"',
            html, re.I,
        ):
            t = _clean(m.group(1))
            if len(t) > len(desc):
                desc = t
        if len(desc) >= 20:
            text = desc
        elif title and len(title) >= 10:
            text = title
        else:
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
