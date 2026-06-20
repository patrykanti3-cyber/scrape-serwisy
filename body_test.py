import asyncio, sys
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode
sys.stdout.reconfigure(encoding="utf-8")
URL = sys.argv[1]
async def main():
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000,
                           wait_until="domcontentloaded", css_selector="article.article-content")
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as c:
        r = await c.arun(URL, config=run)
        md = r.markdown.raw_markdown if hasattr(r.markdown, "raw_markdown") else str(r.markdown)
        open("body.md", "w", encoding="utf-8").write(md)
        print(md)
asyncio.run(main())
