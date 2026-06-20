"""Dump several 24slupsk.pl pages to UTF-8 files + list internal/external links."""
import asyncio, json, sys
from urllib.parse import urlparse
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

PAGES = {
    "home":     "https://24slupsk.pl/",
    "parafie":  "https://24slupsk.pl/info/parafie",
    "przydatne":"https://24slupsk.pl/kategorie/przydatne",
}

async def main():
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000,
                           wait_until="domcontentloaded")
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as c:
        for name, url in PAGES.items():
            r = await c.arun(url, config=run)
            if not r.success:
                print(name, "FAIL", r.error_message); continue
            md = r.markdown.raw_markdown if hasattr(r.markdown, "raw_markdown") else str(r.markdown)
            open(f"slupsk_{name}.md", "w", encoding="utf-8").write(md)
            ext = []
            for l in (r.links.get("external", []) if r.links else []):
                h = l.get("href") if isinstance(l, dict) else l
                if h: ext.append(h)
            intl = []
            for l in (r.links.get("internal", []) if r.links else []):
                h = l.get("href") if isinstance(l, dict) else l
                if h: intl.append(h)
            ext_dom = {}
            for h in ext:
                d = urlparse(h).netloc.replace("www.", "")
                ext_dom[d] = ext_dom.get(d, 0) + 1
            print(f"\n=== {name} ({url}) md_len={len(md)} internal={len(intl)} external={len(ext)}")
            print("  external domains:", json.dumps(dict(sorted(ext_dom.items(), key=lambda x:-x[1])[:25]), ensure_ascii=False))

asyncio.run(main())
