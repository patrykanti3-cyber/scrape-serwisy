"""srcfeeds — scrape news from ORIGINAL upstream sources (police / gmina / miasto),
the same sources the serwisylokalne.pl portals pull from (identified via footprints:
"na podstawie: <źródło>", "FOT. <źródło>", "Ilustracja ... z zewnętrznego źródła (<źródło>)").

NOT a portal scraper. Pulls from official sites so the content can be re-edited (LLM)
and republished with proper attribution.

Pipeline:  sources.json --> [police_rss | municipal_html] --> NewsItem --> data/raw/<city>.jsonl
Next stage: rewrite.py feeds NewsItem.body into Ollama to produce a fresh article.
"""
from __future__ import annotations
import asyncio, json, re, sys, hashlib, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

from cleaners import clean, find_date_iso, clean_police_body, clean_municipal_body

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
DATA_RAW = ROOT.parent / "data" / "raw"
DATA_RAW.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (compatible; srcfeeds/1.0; +local research)"


# --------------------------------------------------------------------------- model
@dataclass
class NewsItem:
    id: str
    city: str
    source_type: str            # police | municipal
    source_name: str            # e.g. "KMP w Słupsku"
    source_credit: str          # short credit used in attribution / photo credit
    source_url: str             # canonical original URL
    title: str
    lead: str = ""
    body: str = ""
    image_url: str = ""
    published: str = ""         # ISO 8601 if known
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def attribution(self) -> str:
        return (f"na podstawie: {self.source_credit}.\n"
                f"Ilustracja wykorzystana w artykule została pobrana z zewnętrznego źródła "
                f"({self.source_credit}).")


def mk_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- RSS
def http_get(url: str, timeout: int = 25, attempts: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500:           # 4xx won't change on retry
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        if i < attempts - 1:
            time.sleep(2 ** i)          # 1s, 2s backoff
    raise last


def discover_police_feed(base: str) -> str | None:
    """Police gov.pl pages expose <link rel=alternate type=application/rss+xml title=Wiadomości href=...>."""
    try:
        html = http_get(base.rstrip("/") + "/").decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! discover_police_feed {base}: {e}")
        return None
    links = re.findall(
        r'<link[^>]+type="application/rss\+xml"[^>]*>', html, re.IGNORECASE)
    best = None
    for tag in links:
        href = re.search(r'href="([^"]+)"', tag)
        title = re.search(r'title="([^"]*)"', tag)
        if not href:
            continue
        url = urljoin(base, href.group(1))
        if title and "wiadomo" in title.group(1).lower():
            return url
        best = best or url
    return best


def parse_rss(xml_bytes: bytes) -> list[dict]:
    items = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        def g(tag):
            el = it.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        pub = g("pubDate")
        try:
            pub_iso = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat() if pub else ""
        except Exception:
            pub_iso = ""
        items.append({"title": g("title"), "link": g("link"),
                      "description": g("description"), "published": pub_iso})
    return items


# --------------------------------------------------------------------------- crawl helpers
def md_of(res) -> str:
    m = res.markdown
    return m.raw_markdown if hasattr(m, "raw_markdown") else str(m)


def og_image(html: str) -> str:
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html or "", re.IGNORECASE)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------- adapters
async def scrape_police(crawler, run, city: str, src: dict, limit: int) -> list[NewsItem]:
    base = src["base"]
    feed = discover_police_feed(base)
    if not feed:
        print(f"  ! no RSS for {src['name']}")
        return []
    print(f"  [police] feed: {feed}")
    items = parse_rss(http_get(feed))[:limit]
    urls = [it["link"] for it in items if it["link"]]
    out: list[NewsItem] = []
    results = await crawler.arun_many(
        urls, config=run.clone(target_elements=["article.txt"]))
    by_url = {r.url: r for r in results}
    for it in items:
        r = by_url.get(it["link"])
        image, lead, body = "", it["description"], it["description"]
        if r and r.success:
            image = og_image(r.html)
            if not image:                      # head may be scoped out -> cheap raw fetch
                try:
                    image = og_image(http_get(it["link"]).decode("utf-8", "replace"))
                except Exception:
                    pass
            l2, b2 = clean_police_body(md_of(r))
            if len(b2) > len(clean(body)):
                body = b2
            if l2:
                lead = l2
        out.append(NewsItem(
            id=mk_id(it["link"]), city=city, source_type="police",
            source_name=src["name"], source_credit=src.get("credit", src["name"]),
            source_url=it["link"], title=clean(it["title"]),
            lead=clean(lead), body=clean(body), image_url=image,
            published=it["published"]))
    return out


async def scrape_municipal(crawler, run, city: str, src: dict, limit: int) -> list[NewsItem]:
    base = src["base"]
    marker = src.get("article_marker", "/aktualnosci/")
    sel = src.get("content_selector", "main")
    # 1) gather article links from listing pages
    art_urls: list[str] = []
    seen = set()
    listres = await crawler.arun_many(src["listing"], config=run)
    for r in listres:
        if not r.success:
            print(f"  ! listing fail {r.url}: {r.error_message}")
            continue
        for l in (r.links.get("internal", []) if r.links else []):
            h = l.get("href") if isinstance(l, dict) else l
            if not h:
                continue
            full = urljoin(r.url, h)
            p = urlparse(full)
            if p.netloc.replace("www.", "") != urlparse(base).netloc.replace("www.", ""):
                continue
            path = p.path
            if marker in path and path.rstrip("/").split("/")[-1].count("-") >= 2 and full not in seen:
                seen.add(full)
                art_urls.append(full)
    art_urls = art_urls[:limit]
    print(f"  [municipal] {src['name']}: {len(art_urls)} article links")
    out: list[NewsItem] = []
    if not art_urls:
        return out
    results = await crawler.arun_many(art_urls, config=run.clone(target_elements=[sel]))
    for r in results:
        if not r.success:
            continue
        md = md_of(r)
        title, lead, body = clean_municipal_body(
            md, end_markers=src.get("end_markers"), noise_patterns=src.get("noise_patterns"))
        if not title:
            title = clean((r.metadata or {}).get("title", ""))
        image = og_image(r.html)
        if not image:
            try:
                image = og_image(http_get(r.url).decode("utf-8", "replace"))
            except Exception:
                pass
        out.append(NewsItem(
            id=mk_id(r.url), city=city, source_type="municipal",
            source_name=src["name"], source_credit=src.get("credit", src["name"]),
            source_url=r.url, title=title, lead=lead, body=body,
            image_url=image, published=find_date_iso(body)))
    return out


# --------------------------------------------------------------------------- pipeline
async def scrape_city(city_slug: str, cfg: dict, limit: int) -> list[NewsItem]:
    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000,
                           wait_until="domcontentloaded", verbose=False)
    items: list[NewsItem] = []
    async with AsyncWebCrawler(config=browser) as crawler:
        for src in cfg["sources"]:
            print(f"--- {city_slug} :: {src['type']} :: {src['name']}")
            try:
                if src["type"] == "police_rss":
                    items += await scrape_police(crawler, run, cfg["city"], src, limit)
                elif src["type"] == "municipal_html":
                    items += await scrape_municipal(crawler, run, cfg["city"], src, limit)
                else:
                    print(f"  ! unknown source type {src['type']}")
            except Exception as e:
                print(f"  ! error in {src['name']}: {e!r}")
    # drop category/empty stubs — log each drop with its reason
    kept: list[NewsItem] = []
    for it in items:
        if not it.title:
            print(f"  (drop no-title: {it.source_url})")
        elif len(it.body) < 250:
            print(f"  (drop short body={len(it.body)}: {it.source_url})")
        else:
            kept.append(it)
    items = kept
    return items


async def main():
    registry = json.load(open(ROOT / "sources.json", encoding="utf-8"))
    cities = sys.argv[1].split(",") if len(sys.argv) > 1 else list(registry.keys())
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    for slug in cities:
        if slug not in registry:
            print(f"skip unknown city {slug}"); continue
        items = await scrape_city(slug, registry[slug], limit)
        path = DATA_RAW / f"{slug}.jsonl"
        if not items:
            print(f"==> {slug}: 0 items — keeping existing {path} (not overwriting)")
            continue
        with open(path, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
        print(f"==> {slug}: {len(items)} items -> {path}")

if __name__ == "__main__":
    asyncio.run(main())
