"""Scrape serwisylokalne.pl network portals and extract the article "source"
footprint: Autor / Materiał źródłowy (link) / na podstawie / Ilustracja...zewnętrznego źródła.

Usage:  python scrape_sources.py [max_articles_per_portal] [max_portals]
Writes: results.jsonl  (one JSON record per crawled article)
        homepages.jsonl (per-portal candidate-link counts)
"""
import asyncio, json, re, sys, io
from pathlib import Path
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
CITIES = json.load(open(r"C:\Users\User\polska-portal\data\cities.json", encoding="utf-8"))

MAX_PER_PORTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 6
MAX_PORTALS = int(sys.argv[2]) if len(sys.argv) > 2 else len(CITIES)

PORTALS = [(c["slug"], c["domain"], c["portalName"]) for c in CITIES][:MAX_PORTALS]
DOMAINS = {d for _, d, _ in PORTALS}

# ---- footprint extractors -------------------------------------------------
AUTHOR_RE      = re.compile(r"Autor:\s*([^\n\[]+)")
ZRODLO_LINK_RE = re.compile(r"Materia[łl] [źz]r[óo]d[łl]owy:\s*\[([^\]]+)\]\(([^)]+)\)", re.IGNORECASE)
ZRODLO_TXT_RE  = re.compile(r"Materia[łl] [źz]r[óo]d[łl]owy:\s*([^\n\[]+)", re.IGNORECASE)
NAPODSTAWIE_RE = re.compile(r"na podstawie\s*:?\s*([^\n]+)", re.IGNORECASE)
ILUSTRACJA_RE  = re.compile(
    r"Ilustracja wykorzystana w artykule zosta[łl]a pobrana z zewn[ęe]trznego [źz]r[óo]d[łl]a\s*\(([^)]*)\)",
    re.IGNORECASE,
)

def is_article_url(u: str, domain: str) -> bool:
    p = urlparse(u)
    if p.netloc.replace("www.", "") != domain:
        return False
    path = p.path.strip("/")
    if not path:
        return False
    bad = ("kategoria", "category", "tag", "author", "autor", "/page", "wydarzenie",
           "kontakt", "regulamin", "polityka", "o-nas", "wp-", "feed", "ebook")
    low = path.lower()
    if any(b in low for b in bad):
        return False
    last = path.split("/")[-1]
    return last.count("-") >= 2

def md_of(res) -> str:
    m = res.markdown
    return m.raw_markdown if hasattr(m, "raw_markdown") else str(m)

def extract(md: str) -> dict:
    rec = {}
    m = AUTHOR_RE.search(md);      rec["author"] = m.group(1).strip() if m else None
    m = ZRODLO_LINK_RE.search(md)
    if m:
        rec["source_title"]  = m.group(1).strip()
        rec["source_url"]    = m.group(2).strip()
        rec["source_domain"] = urlparse(m.group(2)).netloc.replace("www.", "").lower()
    else:
        mt = ZRODLO_TXT_RE.search(md)
        rec["source_text"] = mt.group(1).strip() if mt else None
    m = NAPODSTAWIE_RE.search(md); rec["na_podstawie"] = m.group(1).strip() if m else None
    m = ILUSTRACJA_RE.search(md);  rec["illustration_source"] = (m.group(1).strip() or "(puste)") if m else None
    rec["has_footprint"] = any(rec.get(k) for k in
                               ("author", "source_url", "source_text", "na_podstawie", "illustration_source"))
    return rec

# ---- crawl ----------------------------------------------------------------
async def main():
    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000,
                           wait_until="domcontentloaded", verbose=False)

    home_urls = [f"https://{d}/" for _, d, _ in PORTALS]
    dom_by_base = {f"https://{d}/": d for _, d, _ in PORTALS}

    article_urls = []           # (domain, url)
    home_log = open(ROOT / "homepages.jsonl", "w", encoding="utf-8")

    async with AsyncWebCrawler(config=browser) as crawler:
        # ---- Stage A: homepages -> article candidates ----
        print(f"Stage A: crawling {len(home_urls)} homepages...", flush=True)
        results = await crawler.arun_many(home_urls, config=run)
        for r in results:
            base = r.url if r.url.endswith("/") else r.url + "/"
            domain = dom_by_base.get(base) or urlparse(r.url).netloc.replace("www.", "")
            if not r.success:
                home_log.write(json.dumps({"domain": domain, "ok": False, "err": r.error_message}, ensure_ascii=False) + "\n")
                continue
            links = r.links.get("internal", []) if r.links else []
            hrefs = []
            for l in links:
                h = l.get("href") if isinstance(l, dict) else l
                if h:
                    hrefs.append(urljoin(r.url, h))
            arts = sorted({h for h in hrefs if is_article_url(h, domain)})
            home_log.write(json.dumps({"domain": domain, "ok": True,
                                       "candidates": len(arts)}, ensure_ascii=False) + "\n")
            for a in arts[:MAX_PER_PORTAL]:
                article_urls.append((domain, a))
        home_log.close()
        print(f"Stage A done. Collected {len(article_urls)} article URLs.", flush=True)

        # ---- Stage B: crawl articles, extract footprint ----
        dom_of = {u: d for d, u in article_urls}
        urls = [u for _, u in article_urls]
        out = open(ROOT / "results.jsonl", "w", encoding="utf-8")
        n = 0; n_fp = 0
        print(f"Stage B: crawling {len(urls)} articles...", flush=True)
        results = await crawler.arun_many(urls, config=run)
        for r in results:
            domain = dom_of.get(r.url, urlparse(r.url).netloc.replace("www.", ""))
            if not r.success:
                rec = {"domain": domain, "url": r.url, "ok": False, "err": r.error_message}
            else:
                rec = {"domain": domain, "url": r.url, "ok": True}
                rec.update(extract(md_of(r)))
                if rec.get("has_footprint"):
                    n_fp += 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n += 1
            if n % 25 == 0:
                print(f"  {n}/{len(urls)} crawled, {n_fp} with footprint", flush=True)
        out.close()
        print(f"Stage B done. {n} articles, {n_fp} with footprint. -> results.jsonl", flush=True)

asyncio.run(main())
