"""Audit all 113 cities in sources.json for RSS feeds on municipal websites.

Checks:
1. <link rel="alternate" type="application/rss+xml"> in homepage HTML
2. Common RSS paths: /rss, /feed, /rss.xml, /aktualnosci.xml, /feed.xml
3. Listing page HTML for RSS discovery

Output: report of cities with RSS feeds + their feed URLs.
"""
from __future__ import annotations
import json, ssl, re, sys, urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

UA = "Mozilla/5.0 (compatible; srcfeeds/1.0; +local research)"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ROOT = Path(__file__).resolve().parent.parent
SOURCES = json.load(open(ROOT / "srcfeeds" / "sources.json", encoding="utf-8"))

RSS_LINK_RE = re.compile(
    r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]+href="([^"]+)"',
    re.IGNORECASE,
)
FEED_PATHS = ["/rss", "/feed", "/rss.xml", "/feed.xml", "/aktualnosci.xml", "/rss/"]

def fetch(url: str, timeout: int = 12) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=timeout, context=ctx).read().decode("utf-8", "replace")
    except Exception:
        return None

def check_rss(url: str) -> str | None:
    html = fetch(url)
    if not html:
        return None
    for m in RSS_LINK_RE.finditer(html):
        feed_url = urljoin(url, m.group(1))
        return feed_url
    for path in FEED_PATHS:
        candidate = urljoin(url + "/", path)
        resp = fetch(candidate, timeout=8)
        if resp and ("<rss" in resp or "<feed" in resp):
            return candidate
    return None

def main():
    results = {}
    cities = list(SOURCES.keys())
    for i, slug in enumerate(cities):
        src = SOURCES[slug]
        municipal = None
        for s in src["sources"]:
            if s["type"] == "municipal_html" and "urząd" in s["name"].lower():
                municipal = s
                break
        if not municipal:
            municipal = src["sources"][0] if src["sources"] else None
        if not municipal:
            results[slug] = {"rss": None, "reason": "no municipal source"}
            continue

        base = municipal.get("base", "")
        listing = municipal.get("listing", [base])[0] if municipal.get("listing") else base

        sys.stdout.write(f"\r[{i+1}/{len(cities)}] {slug:20s}")
        sys.stdout.flush()

        feed = check_rss(base)
        if not feed and listing != base:
            feed = check_rss(listing)
        if not feed:
            for s in src["sources"]:
                if s.get("base") and s["base"] != base:
                    feed = check_rss(s["base"])
                    if feed:
                        break

        if feed:
            results[slug] = {"rss": feed, "base": base}
        else:
            results[slug] = {"rss": None, "base": base}

    print("\n")
    with_rss = {k: v for k, v in results.items() if v["rss"]}
    without_rss = {k: v for k, v in results.items() if not v["rss"]}
    print(f"WITH RSS: {len(with_rss)} cities")
    for slug, info in sorted(with_rss.items()):
        print(f"  {slug:20s}  {info['rss']}")
    print(f"\nWITHOUT RSS: {len(without_rss)} cities")
    print(f"  {', '.join(sorted(without_rss.keys())[:20])}...")

    out = ROOT / "srcfeeds" / "rss_audit.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nReport saved to {out}")

if __name__ == "__main__":
    main()
