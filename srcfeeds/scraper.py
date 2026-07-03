"""srcfeeds — scrape news from ORIGINAL upstream sources (police / gmina / miasto),
the same sources the serwisylokalne.pl portals pull from (identified via footprints:
"na podstawie: <źródło>", "FOT. <źródło>", "Ilustracja ... z zewnętrznego źródła (<źródło>)").

NOT a portal scraper. Pulls from official sites so the content can be re-edited (LLM)
and republished with proper attribution.

Pipeline:  sources.json --> [police_rss | municipal_html] --> NewsItem --> data/raw/<city>.jsonl
Next stage: rewrite.py feeds NewsItem.body into Ollama to produce a fresh article.
"""
from __future__ import annotations
import asyncio, json, re, sys, hashlib, ssl, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

from cleaners import clean, find_date_iso, clean_police_body, clean_municipal_body
from discovery import (
    anchor_hrefs,
    filter_article_urls,
    preflight_discover,
    parse_sitemap_urls,
)
from extract import (
    candidate_selectors,
    extract_body_with_fallbacks,
    pagination_links,
)
from event_facts import extract_event_facts

# Quality Gate minimum body length (chars) by source type — matches the drop
# thresholds in scrape_city; also the trigger for trying fallback selectors.
BODY_MIN = {"police": 120, "municipal": 250}
MAX_PAGINATION_PAGES = 3

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
DATA_RAW = ROOT.parent / "data" / "raw"
DATA_RAW.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (compatible; srcfeeds/1.0; +local research)"
DATA_EVENTS = ROOT.parent / "data" / "raw" / "events"
DATA_EVENTS.mkdir(parents=True, exist_ok=True)

# Categories a dedicated section listing may assert deterministically. "wiadomosci"
# and "na-sygnale" are NOT here: general news is left to the LLM, police is fixed.
CATEGORY_HINTS = {"sport", "kultura", "biznes", "ogloszenia"}


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
    category_hint: str = ""     # deterministic category from a dedicated section listing
                                # (sport|kultura|biznes|ogloszenia); "" => LLM classifies
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def attribution(self) -> str:
        return (f"na podstawie: {self.source_credit}.\n"
                f"Ilustracja wykorzystana w artykule została pobrana z zewnętrznego źródła "
                f"({self.source_credit}).")


@dataclass
class EventItem:
    """A scraped event listing entry — facts are extracted later (rewrite_events.py)
    by the LLM into Strapi Event fields (startsAt/endsAt/location)."""
    id: str
    city: str
    source_name: str
    source_credit: str
    source_url: str
    title: str
    lead: str = ""              # structured "Data: …, Godzina: …, Miejsce: …" anchor
    body: str = ""              # raw text the LLM mines for date/time/location
    image_url: str = ""
    published: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def mk_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- RSS
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def http_get(url: str, timeout: int = 25, attempts: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        if i < attempts - 1:
            time.sleep(2 ** i)
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


def meta_date(html: str) -> str:
    """Publish date from <meta article:published_time> — more reliable than
    scanning body text. Returns ISO yyyy-mm-dd or ''."""
    m = re.search(
        r'<meta[^>]+(?:property|name)="(?:article:published_time|datePublished|date)"[^>]+content="([^"]+)"',
        html or "", re.IGNORECASE)
    if not m:
        return ""
    iso = re.match(r"(\d{4}-\d{2}-\d{2})", m.group(1).strip())
    return iso.group(1) if iso else ""


def _wait_for(src: dict):
    """crawl4ai `wait_for` derived from a per-source CSS selector.

    Set `js_wait_selector` in sources.json to make Playwright block until that
    element appears (SPA content that arrives after initial paint). Returns None
    when unset (crawl4ai then just honours wait_until)."""
    sel = src.get("js_wait_selector")
    return f"css:{sel}" if sel else None


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
        urls, config=run.clone(target_elements=["article.txt"], wait_for=_wait_for(src)))
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


async def scrape_municipal_rss(crawler, run, city: str, src: dict, limit: int) -> list[NewsItem]:
    feed = src.get("feed") or discover_police_feed(src["base"])
    if not feed:
        print(f"  ! no RSS feed for {src['name']}")
        return []
    print(f"  [municipal_rss] feed: {feed}")
    items = parse_rss(http_get(feed))[:limit]
    urls = [it["link"] for it in items if it["link"]]
    sel = src.get("content_selector", "main")
    out: list[NewsItem] = []
    results = await crawler.arun_many(
        urls, config=run.clone(target_elements=[sel], wait_for=_wait_for(src)))
    by_url = {r.url: r for r in results}
    for it in items:
        r = by_url.get(it["link"])
        image, lead, body = "", it["description"], it["description"]
        if r and r.success:
            image = og_image(r.html)
            if not image:
                try:
                    image = og_image(http_get(it["link"]).decode("utf-8", "replace"))
                except Exception:
                    pass
            title2, l2, b2 = clean_municipal_body(
                md_of(r), end_markers=src.get("end_markers"),
                noise_patterns=src.get("noise_patterns"))
            if b2 and len(b2) > len(clean(body)):
                body = b2
            if l2:
                lead = l2
            if not it["title"] and title2:
                it["title"] = title2
        out.append(NewsItem(
            id=mk_id(it["link"]), city=city, source_type="municipal",
            source_name=src["name"], source_credit=src.get("credit", src["name"]),
            source_url=it["link"], title=clean(it["title"]),
            lead=clean(lead), body=clean(body), image_url=image,
            published=it["published"]))
    return out


async def scrape_municipal(crawler, run, city: str, src: dict, limit: int) -> list[NewsItem]:
    base = src["base"]
    marker = src.get("article_marker", "/aktualnosci/")
    sel = src.get("content_selector", "main")
    wait_for = _wait_for(src)
    list_run = run.clone(wait_for=wait_for)

    # 0) Pre-flight: RSS/sitemap discovery is far more stable than JS-rendered
    #    HTML. If the site advertises an RSS feed and this source has no explicit
    #    feed, scrape that instead of pushing Playwright at an SPA.
    pre = preflight_discover(base, http_get)
    if pre["rss"] and not src.get("feed") and not src.get("rss"):
        print(f"  [preflight] discovered RSS {pre['rss']} — using RSS instead of HTML")
        return await scrape_municipal_rss(crawler, run, city, {**src, "feed": pre["rss"]}, limit)

    art_urls: list[str] = []
    seen: set[str] = set()
    render_htmls: list[tuple[str, str]] = []

    # 1) crawl4ai link graph from the listing pages (JS rendered via networkidle),
    #    following pagination when `pagination_selector` is configured (urzędy
    #    paginate their announcement listings).
    pagination_selector = src.get("pagination_selector")
    pages_to_crawl = list(src["listing"])
    visited_pages: set[str] = set()
    for _round in range(MAX_PAGINATION_PAGES + 1):
        batch = [u for u in pages_to_crawl if u not in visited_pages]
        if not batch:
            break
        visited_pages.update(batch)
        pages_to_crawl = []
        listres = await crawler.arun_many(batch, config=list_run)
        for r in listres:
            if not r.success:
                print(f"  ! listing fail {r.url}: {r.error_message}")
                continue
            if r.html:
                render_htmls.append((r.url, r.html))
            internal = [
                (l.get("href") if isinstance(l, dict) else l)
                for l in (r.links.get("internal", []) if r.links else [])
            ]
            for full in filter_article_urls(internal, r.url, base, marker):
                if full not in seen:
                    seen.add(full)
                    art_urls.append(full)
            if pagination_selector and len(art_urls) < limit and r.html:
                for nxt in pagination_links(r.html, r.url, base, pagination_selector):
                    if nxt not in visited_pages:
                        pages_to_crawl.append(nxt)
        if len(art_urls) >= limit:
            break

    def _add(urls) -> int:
        n = 0
        for full in urls:
            if full not in seen:
                seen.add(full)
                art_urls.append(full)
                n += 1
        return n

    # 2) SPA fallback — the link graph came back empty (common on JS portals):
    #    parse <a href> straight from the *rendered* DOM HTML. Try the configured
    #    marker first, then a marker-agnostic pass (same-host + article-like slug)
    #    so a stale `article_marker` no longer blocks recovery.
    if not art_urls:
        for m in (marker, ""):
            for page_url, html in render_htmls:
                _add(filter_article_urls(anchor_hrefs(html), page_url, base, m))
            if art_urls:
                tag = "" if m else " (marker-agnostic)"
                print(f"  [spa-fallback] recovered {len(art_urls)} links from rendered DOM{tag}")
                break

    # 3) Sitemap fallback (no browser at all) — pull article URLs from the
    #    sitemaps discovered in pre-flight. Same marker-then-relaxed strategy.
    if not art_urls:
        sm_locs: list[tuple[str, str]] = []
        for sm in pre["sitemaps"][:3]:
            try:
                sm_locs += [(sm, u) for u in parse_sitemap_urls(http_get(sm))]
            except Exception as e:
                print(f"  ! sitemap {sm}: {e}")
        for m in (marker, ""):
            for sm, u in sm_locs:
                _add(filter_article_urls([u], sm, base, m))
            if art_urls:
                tag = "" if m else " (marker-agnostic)"
                print(f"  [sitemap] recovered {len(art_urls)} links from sitemap{tag}")
                break

    art_urls = art_urls[:limit]
    print(f"  [municipal] {src['name']}: {len(art_urls)} article links")
    out: list[NewsItem] = []
    if not art_urls:
        return out
    results = await crawler.arun_many(
        art_urls, config=run.clone(target_elements=[sel], wait_for=wait_for))
    # police sites are scraped as HTML too, but must map to the na-sygnale
    # category — tag by source, not by adapter.
    stype = "police" if ("policja.gov.pl" in base or src["name"].lower().startswith("policja")) else "municipal"
    # A dedicated section listing (e.g. .../sport) carries a deterministic
    # category; otherwise leave it empty so the LLM classifies by topic.
    cat_hint = (src.get("category") or "").strip().lower()
    if cat_hint not in CATEGORY_HINTS:
        cat_hint = ""
    min_len = BODY_MIN.get(stype, 250)
    fallbacks = src.get("fallback_selectors", [])
    for r in results:
        if not r.success:
            continue
        md = md_of(r)
        title, lead, body = clean_municipal_body(
            md, end_markers=src.get("end_markers"), noise_patterns=src.get("noise_patterns"))
        # Resilience: primary content_selector produced too little (a CSS class
        # likely changed) -> try fallback_selectors on the rendered HTML until
        # the body clears the Quality Gate.
        if len(body) < min_len and getattr(r, "html", None):
            alt = extract_body_with_fallbacks(
                r.html, candidate_selectors(sel, fallbacks), min_len,
                end_markers=src.get("end_markers"), noise_patterns=src.get("noise_patterns"))
            if len(alt["body"]) > len(body):
                title = title or alt["title"]
                lead = alt["lead"] or lead
                body = alt["body"]
                if alt["selector"] and alt["selector"] != sel:
                    print(f"  [fallback-selector] {r.url} -> '{alt['selector']}' body={len(body)}")
        if not title:
            title = clean((r.metadata or {}).get("title", ""))
        image = og_image(r.html)
        if not image:
            try:
                image = og_image(http_get(r.url).decode("utf-8", "replace"))
            except Exception:
                pass
        out.append(NewsItem(
            id=mk_id(r.url), city=city, source_type=stype,
            source_name=src["name"], source_credit=src.get("credit", src["name"]),
            source_url=r.url, title=title, lead=lead, body=body,
            image_url=image, published=meta_date(r.html) or find_date_iso(body),
            category_hint="" if stype == "police" else cat_hint))
    return out


async def scrape_events(crawler, run, city: str, src: dict, limit: int) -> list[EventItem]:
    """Harvest event listing entries. Same link-gathering as municipal, but the
    body is kept raw — rewrite_events.py mines date/time/location via the LLM."""
    base = src["base"]
    marker = src.get("article_marker", "/wydarzenia/")
    sel = src.get("content_selector", "main")
    art_urls: list[str] = []
    seen = set()
    listres = await crawler.arun_many(src["listing"], config=run)
    for r in listres:
        if not r.success:
            print(f"  ! events listing fail {r.url}: {r.error_message}")
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
            if marker.lower() in path.lower() and path.rstrip("/").split("/")[-1].count("-") >= 2 and full not in seen:
                seen.add(full)
                art_urls.append(full)
    art_urls = art_urls[:limit]
    print(f"  [events] {src['name']}: {len(art_urls)} event links")
    out: list[EventItem] = []
    if not art_urls:
        return out
    results = await crawler.arun_many(art_urls, config=run.clone(target_elements=[sel]))
    for r in results:
        if not r.success:
            continue
        md = md_of(r)
        title, _lead, body = clean_municipal_body(
            md, end_markers=src.get("end_markers"), noise_patterns=src.get("noise_patterns"))
        if not title:
            title = clean((r.metadata or {}).get("title", ""))
        image = og_image(r.html)
        if not image:
            try:
                image = og_image(http_get(r.url).decode("utf-8", "replace"))
            except Exception:
                pass
        # Deterministic date/time/location anchor BEFORE the LLM stage, so the
        # model can't hallucinate the event's date/time.
        facts = extract_event_facts(f"{title}\n{body}")
        if facts["lead"]:
            print(f"  [event-facts] {facts['lead']}")
        out.append(EventItem(
            id=mk_id(r.url), city=city,
            source_name=src["name"], source_credit=src.get("credit", src["name"]),
            source_url=r.url, title=title, lead=facts["lead"], body=body, image_url=image,
            published=meta_date(r.html) or facts["date_iso"] or find_date_iso(body)))
    return out


# --------------------------------------------------------------------------- pipeline
async def scrape_city(city_slug: str, cfg: dict, limit: int) -> tuple[list[NewsItem], list[EventItem]]:
    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000,
                           wait_until="networkidle", verbose=False)
    items: list[NewsItem] = []
    events: list[EventItem] = []
    async with AsyncWebCrawler(config=browser) as crawler:
        for src in cfg["sources"]:
            print(f"--- {city_slug} :: {src['type']} :: {src['name']}")
            try:
                if src["type"] == "police_rss":
                    items += await scrape_police(crawler, run, cfg["city"], src, limit)
                elif src["type"] == "municipal_rss":
                    items += await scrape_municipal_rss(crawler, run, cfg["city"], src, limit)
                elif src["type"] == "events_html":
                    events += await scrape_events(crawler, run, cfg["city"], src, limit)
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
        elif len(it.body) < (120 if it.source_type == "police" else 250):
            print(f"  (drop short body={len(it.body)} type={it.source_type}: {it.source_url})")
        else:
            kept.append(it)
    items = kept
    # events: keep anything with a title; date/location are mined downstream
    events = [e for e in events if e.title and len(e.body) >= 40]
    return items, events


def _write_jsonl(path: Path, rows: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for it in rows:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")


async def main():
    registry = json.load(open(ROOT / "sources.json", encoding="utf-8"))
    arg1 = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg1 == "--all":
        cities = list(registry.keys())
    else:
        cities = arg1.split(",") if arg1 else list(registry.keys())
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 5
    city_timeout = 120

    ok, failed, total_items = [], [], 0
    for slug in cities:
        if slug not in registry:
            print(f"skip unknown city {slug}"); continue
        try:
            items, events = await asyncio.wait_for(
                scrape_city(slug, registry[slug], limit), timeout=city_timeout)
        except asyncio.TimeoutError:
            print(f"==> {slug}: TIMEOUT ({city_timeout}s) — skipping")
            failed.append(slug); continue
        except Exception as e:
            print(f"==> {slug}: ERROR: {e!r} — skipping")
            failed.append(slug); continue
        path = DATA_RAW / f"{slug}.jsonl"
        if items:
            _write_jsonl(path, items)
            print(f"==> {slug}: {len(items)} items -> {path}")
            ok.append(slug); total_items += len(items)
        else:
            print(f"==> {slug}: 0 items — keeping existing {path} (not overwriting)")
            failed.append(slug)
        epath = DATA_EVENTS / f"{slug}.jsonl"
        if events:
            _write_jsonl(epath, events)
            print(f"==> {slug}: {len(events)} events -> {epath}")

    print(f"\n{'='*60}")
    print(f"SUMMARY: OK={len(ok)}  FAILED={len(failed)}  TOTAL={total_items} articles")
    if failed:
        print(f"Failed: {', '.join(failed)}")

if __name__ == "__main__":
    asyncio.run(main())
