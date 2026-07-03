"""Pure, network-free helpers for the scraper's SPA/JS hardening.

Split out of scraper.py so they can be unit-tested without crawl4ai/Playwright:
- anchor_hrefs / filter_article_urls: extract article links from *rendered* DOM
  HTML (fallback when crawl4ai's link graph yields nothing on SPA pages).
- robots_sitemaps / head_rss_link / parse_sitemap_urls / preflight_discover:
  cheap pre-flight discovery of RSS + XML sitemaps (far more stable than
  scraping JS-rendered HTML). `fetch` is injected so tests never hit the network.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse


def strip_www(netloc: str) -> str:
    return (netloc or "").lower().split(":")[0].replace("www.", "")


def same_host(a: str, b: str) -> bool:
    """Compare two hosts/URLs ignoring scheme, port and a leading www."""
    ha = strip_www(urlparse(a).netloc or a)
    hb = strip_www(urlparse(b).netloc or b)
    return bool(ha) and ha == hb


def anchor_hrefs(html: str) -> list[str]:
    """Every href="" from <a> tags in (rendered) HTML — the SPA fallback.

    crawl4ai returns the JS-rendered DOM in `result.html`, so parsing anchors
    here recovers links even when the link graph came back empty.
    """
    return re.findall(r"<a\b[^>]*?\bhref=[\"']([^\"'#][^\"']*)[\"']",
                      html or "", flags=re.IGNORECASE)


def filter_article_urls(hrefs, page_url: str, base: str, marker: str,
                        min_dashes: int = 2) -> list[str]:
    """Keep same-host URLs whose path contains `marker` and whose last segment
    looks like an article slug (>= min_dashes dashes). De-duplicates, preserves
    order. Mirrors the original inline rule so behaviour is unchanged.
    """
    out: list[str] = []
    seen: set[str] = set()
    marker = (marker or "").lower()
    for h in hrefs or []:
        if not h:
            continue
        full = urljoin(page_url, h)
        p = urlparse(full)
        if not same_host(p.netloc, base):
            continue
        path = p.path
        last = path.rstrip("/").split("/")[-1]
        if marker in path.lower() and last.count("-") >= min_dashes and full not in seen:
            seen.add(full)
            out.append(full)
    return out


# --------------------------------------------------------------------------- RSS / sitemap
def robots_sitemaps(robots_txt: str) -> list[str]:
    """Sitemap: URLs declared in robots.txt (case-insensitive), in order."""
    out: list[str] = []
    for line in (robots_txt or "").splitlines():
        m = re.match(r"\s*sitemap\s*:\s*(\S+)", line, flags=re.IGNORECASE)
        if m:
            out.append(m.group(1).strip())
    return out


def head_rss_link(html: str, base: str) -> str | None:
    """First <link rel="alternate" type="application/rss+xml" href="..."> in
    <head>, resolved against `base`. Prefers a feed whose title mentions news
    ("wiadomo"/"aktual") when several are present.
    """
    best = None
    for tag in re.findall(r"<link\b[^>]*>", html or "", flags=re.IGNORECASE):
        if not re.search(r'type=["\']application/(?:rss|atom)\+xml["\']', tag, re.I):
            continue
        href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        if not href:
            continue
        url = urljoin(base, href.group(1).strip())
        title = re.search(r'title=["\']([^"\']*)["\']', tag, re.I)
        if title and re.search(r"wiadomo|aktual|news", title.group(1), re.I):
            return url
        best = best or url
    return best


def parse_sitemap_urls(xml_bytes) -> list[str]:
    """All <loc> URLs from a sitemap or sitemapindex (namespace-agnostic)."""
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    locs: list[str] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() == "loc" and el.text:
            locs.append(el.text.strip())
    return locs


def preflight_discover(base: str, fetch) -> dict:
    """Cheap, network-only-via-`fetch` discovery BEFORE launching Playwright.

    Returns {"rss": <url|None>, "sitemaps": [<url>, ...]}.
    `fetch(url) -> bytes` is injected (tests pass a fake; scraper passes http_get).
    Never raises — discovery is best-effort.
    """
    base = base.rstrip("/") + "/"
    rss = None
    sitemaps: list[str] = []

    # robots.txt -> Sitemap: lines
    try:
        robots = fetch(urljoin(base, "/robots.txt"))
        if isinstance(robots, bytes):
            robots = robots.decode("utf-8", "replace")
        sitemaps = robots_sitemaps(robots)
    except Exception:
        pass

    # homepage <head> -> RSS/Atom link
    try:
        html = fetch(base)
        if isinstance(html, bytes):
            html = html.decode("utf-8", "replace")
        rss = head_rss_link(html, base)
    except Exception:
        pass

    # common fallback sitemap location
    if not sitemaps:
        sitemaps = [urljoin(base, "/sitemap.xml")]

    return {"rss": rss, "sitemaps": sitemaps}
