"""Exploratory: crawl one portal homepage, list internal article links,
then crawl a few of them and search for the source/author/illustration footprint."""
import asyncio, re, sys
from urllib.parse import urljoin, urlparse
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "echowarszawy.pl"
BASE = f"https://{DOMAIN}/"

FOOTPRINT_RE = re.compile(
    r"(na podstawie\s*:|Autor\s*:|Ilustracja wykorzystana w artykule)",
    re.IGNORECASE,
)

def is_article_url(u: str) -> bool:
    p = urlparse(u)
    if p.netloc.replace("www.", "") != DOMAIN:
        return False
    path = p.path.strip("/")
    if not path:
        return False
    bad = ("kategoria", "category", "tag", "author", "autor", "page",
           "kontakt", "regulamin", "polityka", "o-nas", "wp-", "feed")
    if any(b in path.lower() for b in bad):
        return False
    last = path.split("/")[-1]
    return last.count("-") >= 2

async def main():
    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000)
    async with AsyncWebCrawler(config=browser) as crawler:
        res = await crawler.arun(BASE, config=run)
        if not res.success:
            print("HOMEPAGE FAIL:", res.error_message)
            return
        links = res.links.get("internal", [])
        hrefs = []
        for l in links:
            h = l.get("href") if isinstance(l, dict) else l
            if h:
                hrefs.append(urljoin(BASE, h))
        arts = sorted({h for h in hrefs if is_article_url(h)})
        print(f"[{DOMAIN}] internal links: {len(hrefs)}, candidate articles: {len(arts)}")
        for a in arts[:10]:
            print("  ", a)

        for a in arts[:3]:
            r = await crawler.arun(a, config=run)
            if not r.success:
                print("  ARTICLE FAIL:", a, r.error_message)
                continue
            md = r.markdown.raw_markdown if hasattr(r.markdown, "raw_markdown") else str(r.markdown)
            hits = FOOTPRINT_RE.findall(md)
            print(f"\n=== {a}\n    md_len={len(md)} footprint_hits={hits}")
            for m in re.finditer(r"(na podstawie|Autor\s*:|Ilustracja wykorzystana)", md, re.IGNORECASE):
                s = max(0, m.start() - 40)
                e = min(len(md), m.start() + 160)
                print("    ...", md[s:e].replace("\n", " "))

asyncio.run(main())
