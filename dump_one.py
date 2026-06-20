import asyncio, sys
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

URL = sys.argv[1]
async def main():
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as c:
        r = await c.arun(URL, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000))
        md = r.markdown.raw_markdown if hasattr(r.markdown, "raw_markdown") else str(r.markdown)
        open("dump.md", "w", encoding="utf-8").write(md)
        print("wrote dump.md len", len(md))
asyncio.run(main())
