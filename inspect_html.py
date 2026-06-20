import asyncio, re, sys
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode
sys.stdout.reconfigure(encoding="utf-8")
URL = sys.argv[1]
async def main():
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000, wait_until="domcontentloaded")
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as c:
        r = await c.arun(URL, config=run)
        html = r.html or ""
        open("art_full.html", "w", encoding="utf-8").write(html)
        md = r.markdown.raw_markdown if hasattr(r.markdown, "raw_markdown") else str(r.markdown)
        open("art_full.md", "w", encoding="utf-8").write(md)
        print("html_len", len(html), "md_len", len(md))
        # show tags around 'Autor'
        for kw in ["Autor", "rel=\"author\"", "itemprop", "<article", "article-content", "entry-content", "post-content", "class=\"content"]:
            i = html.find(kw)
            print(f"\n--- '{kw}' @ {i} ---")
            if i >= 0:
                print(html[max(0,i-200):i+120].replace("\n"," "))
asyncio.run(main())
