"""build_sources.py — generate + verify scrape sources for ALL network cities.

For each city we establish two ORIGINAL upstream sources (the legitimate feeds the
serwisylokalne.pl portals themselves recycle from):
  * municipal  — the city/gmina office site (authoritative URL from Wikidata P856)
  * police     — https://<slug>.policja.gov.pl (KMP/KPP)

Both are scraped uniformly as `municipal_html`: we auto-discover each site's
"Aktualności" listing page (works on municipal CMSes AND on both police gov.pl
templates), from which the scraper harvests article links. Old-template police
sites additionally expose an RSS <link>, which we record as a bonus.

Output:
  sources.json          — registry consumed by scraper.py
  sources_report.json   — per-city discovery status (for manual gap review)
Run:
  python build_sources.py            # all cities
  python build_sources.py 25         # first N (smoke test)
"""

from __future__ import annotations
import asyncio, json, re, sys, urllib.request, urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
CITIES_PATH = ROOT.parent.parent / "polska-portal" / "data" / "cities.json"
CITIES = json.load(open(CITIES_PATH, encoding="utf-8"))
UA = "Mozilla/5.0 (compatible; srcfeeds/1.0; +local research)"

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

# --- manual overrides --------------------------------------------------------
# municipal base for cities without a Wikidata Q-ID, or where P856 is wrong/dead
MUNI_OVERRIDE = {
    "jaworzno": "https://www.jaworzno.pl",
    "limanowa": "https://miasto.limanowa.pl",
    "piaseczno": "https://www.piaseczno.eu",  # Wikidata P856 pointed to a school domain
    "lodz": "https://lodz.pl",  # uml.lodz.pl bot-blocks headless
    "plock": "https://nowy.plock.eu",
    "ostrow-wielkopolski": "https://umostrow.pl",
    "gniezno": "https://gniezno.eu",
    "tomaszow-mazowiecki": "https://tomaszow-maz.pl",
    "zawiercie": "https://zawiercie.eu",
    "ciechanow": "https://umciechanow.pl",
    "cieszyn": "https://www.cieszyn.pl",
    "skawina": "https://gminaskawina.pl",
    "niepolomice": "https://niepolomice.eu",
    "chorzow": "https://mieszkancy.chorzow.eu",
    "przemysl": "https://przemysl.pl",
    "siemianowice-slaskie": "https://siemianowice.pl",
    "chelm": "https://samorzad.gov.pl/web/miasto-chelm",
}
# police subdomain != slug
POLICE_SUB_OVERRIDE = {
    "warszawa": "ksp",  # Komenda Stołeczna
    "minsk-mazowiecki": "minsk",
    "grodzisk-mazowiecki": "grodzisk",
    "niepolomice": "wieliczka",  # KPP w Wieliczce (powiat wielicki)
    # KWP fallback — miasta bez własnej KMP/KPP
    "radom": "mazowiecka",
    "plock": "mazowiecka",
    "siedlce": "mazowiecka",
    "ostroleka": "mazowiecka",
    "ciechanow": "mazowiecka",
    "zyrardow": "mazowiecka",
    "nowy-dwor-mazowiecki": "mazowiecka",
    "skawina": "malopolska",
    "krzeszowice": "malopolska",
}
# verified listing URLs (deep-probe / manual) keyed by (slug, kind)
LISTING_OVERRIDE = {
    ("zielona-gora", "municipal"): "https://www.zielona-gora.pl/wydarzenia",
    ("zamosc", "municipal"): "https://www.zamosc.pl/news",
    ("nowy-targ", "municipal"): "https://www.nowytarg.pl/aktualnosci",
    ("limanowa", "municipal"): "http://miasto.limanowa.pl/aktualnosci-z-miasta",
    ("lodz", "municipal"): "https://lodz.pl/aktualnosci",
    ("plock", "municipal"): "https://nowy.plock.eu/aktualnosci",
    ("ostrow-wielkopolski", "municipal"): "https://umostrow.pl/aktualnosci",
    ("gniezno", "municipal"): "https://gniezno.eu/aktualnosci-z-miasta",
    ("tomaszow-mazowiecki", "municipal"): "https://tomaszow-maz.pl/aktualnosci",
    ("zawiercie", "municipal"): "https://zawiercie.eu/aktualnosci",
    ("ciechanow", "municipal"): "https://umciechanow.pl/samorzad/aktualnosci",
    ("cieszyn", "municipal"): "https://www.cieszyn.pl/aktualnosci",
    ("skawina", "municipal"): "https://gminaskawina.pl/aktualnosci",
    ("niepolomice", "municipal"): "https://niepolomice.eu/wydarzenia",
    (
        "chorzow",
        "municipal",
    ): "https://mieszkancy.chorzow.eu/category/komunikaty-urzedowe",
    ("przemysl", "municipal"): "https://przemysl.pl/13/aktualnosci.html",
    (
        "piotrkow-trybunalski",
        "police",
    ): "https://piotrkow-trybunalski.policja.gov.pl/ep/aktualnosci",
    ("siemianowice-slaskie", "municipal"): "https://siemianowice.pl",
    ("chelm", "municipal"): "https://samorzad.gov.pl/web/miasto-chelm/aktualnosci",
    ("zakopane", "municipal"): "https://www.zakopane.pl/strefa-miejska/aktualnosci",
    ("sandomierz", "municipal"): "https://sandomierz.eu/1193/357/wydarzenia.html",
}
# article_marker override for non-standard CMS URL schemes
MARKER_OVERRIDE = {
    ("chorzow", "municipal"): "/article/",
    ("gniezno", "municipal"): "/wiadomosci/",
    ("przemysl", "municipal"): ".html",
    ("siemianowice-slaskie", "municipal"): "/20",
    ("chelm", "municipal"): "/miasto-chelm/",
    ("sandomierz", "municipal"): "/357/",
}

AKTUAL_RE = re.compile(r"aktualno[śs]ci", re.I)
FALLBACK_RE = re.compile(r"wiadomo[śs]ci|aktualno[śs][ćc]|/news\b|nowo[śs]ci", re.I)


def norm(u: str) -> str:
    return (u or "").rstrip("/").lower()


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# --- Wikidata official websites ---------------------------------------------
def wikidata_sites(qids):
    out = {}
    for i in range(0, len(qids), 50):
        batch = qids[i : i + 50]
        params = urllib.parse.urlencode(
            {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "claims",
                "format": "json",
            }
        )
        data = json.loads(
            http_get("https://www.wikidata.org/w/api.php?" + params).decode("utf-8")
        )
        for qid, ent in data.get("entities", {}).items():
            try:
                out[qid] = ent["claims"]["P856"][0]["mainsnak"]["datavalue"]["value"]
            except Exception:
                out[qid] = ""
    return out


def municipal_base(city) -> str:
    if city["slug"] in MUNI_OVERRIDE:
        return MUNI_OVERRIDE[city["slug"]]
    return WD.get(city.get("wikidata", ""), "")


def police_base(city) -> str:
    sub = POLICE_SUB_OVERRIDE.get(city["slug"], city["slug"])
    return f"https://{sub}.policja.gov.pl"


# --- listing / rss discovery from a rendered page ---------------------------
def links_from(res):
    out = []
    if res.links:
        for l in res.links.get("internal", []):
            if isinstance(l, dict):
                out.append((l.get("href", ""), (l.get("text") or "").strip()))
            elif l:
                out.append((str(l), ""))
    # also scrape raw anchors (text may differ)
    for m in re.finditer(
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', res.html or "", re.I | re.S
    ):
        txt = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        out.append((m.group(1), txt))
    return out


def is_article_like(path: str) -> bool:
    last = path.rstrip("/").split("/")[-1].lower()
    return bool(
        re.search(r"\d{3,}", last) or last.endswith(".html") or last.count("-") >= 3
    )


KEY_SEG_RE = re.compile(r"aktualno|wiadomo|infotek|nowosci|komunikaty|news", re.I)


def trim_to_section(url: str) -> str:
    """If a listing URL drills past its section root, cut back to the section segment."""
    p = urllib.parse.urlparse(url)
    segs = [s for s in p.path.strip("/").split("/") if s]
    for i, s in enumerate(segs):
        if KEY_SEG_RE.search(s) and not is_article_like("/" + s):
            return f"{p.scheme}://{p.netloc}/" + "/".join(segs[: i + 1])
    return url.split("?")[0].split("#")[0]


def reg_domain(host: str) -> str:
    return ".".join(host.replace("www.", "").split(".")[-2:])


def pick_listing(base_url, res):
    """Return (listing_url, matched_text) or (None, None)."""
    base_host = urllib.parse.urlparse(base_url).netloc.replace("www.", "")
    base_reg = reg_domain(base_host)
    # allow cross-host news sections only for real registrable domains (not shared gov/edu)
    allow_cross = base_reg not in (
        "gov.pl",
        "com.pl",
        "edu.pl",
        "org.pl",
        "home.pl",
        "net.pl",
    )
    same, cross = [], []
    for href, text in links_from(res):
        if not href:
            continue
        full = urllib.parse.urljoin(res.url or base_url, href)
        p = urlparse_safe(full)
        if not p or not p.netloc:
            continue
        host = p.netloc.replace("www.", "")
        path = p.path.rstrip("/")
        if not path:
            continue
        last = path.split("/")[-1].lower()
        t = (text or "").strip().lower()
        text_hit = AKTUAL_RE.search(text or "")
        href_hit = AKTUAL_RE.search(path)
        fb_text = FALLBACK_RE.search(text or "")
        fb_href = FALLBACK_RE.search(path)
        score = 0
        if t in ("aktualności", "aktualnosci", "wiadomości", "wiadomosci"):
            score = 100
        elif text_hit:
            score = 80
        elif AKTUAL_RE.search(last):
            score = 75
        elif href_hit:
            score = 55
        elif fb_text:
            score = 40
        elif fb_href:
            score = 30
        if not score:
            continue
        if is_article_like(path):
            score -= 50
        depth = path.count("/")
        rec = (score, -depth, -len(path), full, (text or "").strip()[:40])
        if host == base_host:
            same.append(rec)
        elif allow_cross and reg_domain(host) == base_reg and score >= 75:
            cross.append(rec)
    pool = same if same else cross
    if not pool:
        return None, None
    pool.sort(reverse=True)
    best = pool[0]
    return trim_to_section(best[3]), best[4]


def find_rss(base_url, res):
    for m in re.finditer(r"<link[^>]+application/rss\+xml[^>]+>", res.html or "", re.I):
        tag = m.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        title = re.search(r'title="([^"]*)"', tag)
        if href and (
            not title
            or "wiadomo" in (title.group(1) or "").lower()
            or "dokumenty/rss" in href.group(1)
        ):
            return urllib.parse.urljoin(res.url or base_url, href.group(1))
    return None


def urlparse_safe(u):
    try:
        return urllib.parse.urlparse(u)
    except Exception:
        return None


def marker_from(listing_url: str) -> str:
    if not listing_url:
        return "/aktualnosci/"
    path = urllib.parse.urlparse(listing_url).path.lower()
    if "aktualno" in path:
        return "/aktualnosci/"
    if "wiadomo" in path:
        return "/wiadomosci/"
    if "infotek" in path:
        return "/infoteka/"
    if "komunikaty" in path:
        return "/komunikaty/"
    if "nowosci" in path:
        return "/nowosci/"
    if "news" in path:
        return "/news/"
    seg = path.rstrip("/").split("/")[-1]
    return f"/{seg}/" if seg else "/aktualnosci/"


# --- main pipeline -----------------------------------------------------------
WD: dict = {}


async def render_all(urls, batch=24, wait="domcontentloaded", delay=1.5, timeout=40000):
    """Render urls concurrently in batches; return {norm(url): result}."""
    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=timeout,
        wait_until=wait,
        verbose=False,
        delay_before_return_html=delay,
    )
    out = {}
    async with AsyncWebCrawler(config=browser) as crawler:
        B = batch
        for i in range(0, len(urls), B):
            chunk = urls[i : i + B]
            print(f"  rendering {i + 1}-{i + len(chunk)} / {len(urls)} ...", flush=True)
            results = await crawler.arun_many(chunk, config=run)
            for r in results:
                out[norm(r.url)] = r
                # also key by the requested url if redirected
            # map requested urls to nearest result by host
            for u in chunk:
                if norm(u) not in out:
                    for r in results:
                        if urllib.parse.urlparse(r.url).netloc.replace(
                            "www.", ""
                        ) == urllib.parse.urlparse(u).netloc.replace("www.", ""):
                            out[norm(u)] = r
                            break
    return out


def discover(base_url, rendered):
    r = rendered.get(norm(base_url))
    if r is None:
        # try host match
        h = urllib.parse.urlparse(base_url).netloc.replace("www.", "")
        for k, v in rendered.items():
            if urllib.parse.urlparse(v.url).netloc.replace("www.", "") == h:
                r = v
                break
    if r is None or not r.success:
        return {"status": "unreachable", "listing": None, "text": None, "rss": None}
    listing, text = pick_listing(base_url, r)
    rss = find_rss(base_url, r)
    status = "ok" if listing else ("rss-only" if rss else "no-listing")
    return {
        "status": status,
        "listing": listing,
        "text": text,
        "rss": rss,
        "final_url": r.url,
    }


def make_src(kind, c, d, base):
    name = f"Urząd — {c['name']}" if kind == "municipal" else f"Policja — {c['name']}"
    credit = f"UM {c['name']}" if kind == "municipal" else f"Policja {c['name']}"
    marker = MARKER_OVERRIDE.get((c["slug"], kind)) or marker_from(d["listing"])
    src = {
        "type": "municipal_html",
        "name": name,
        "credit": credit,
        "base": d.get("final_url") or base,
        "listing": [d["listing"]] if d["listing"] else [],
        "article_marker": marker,
        "content_selector": "main",
    }
    if d.get("rss"):
        src["rss"] = d["rss"]
    return src


def assemble_city(c, rendered):
    slug = c["slug"]
    sources, rep = [], {"city": c["name"]}
    mb = municipal_base(c)
    if mb:
        ov = LISTING_OVERRIDE.get((slug, "municipal"))
        if ov:
            d = {"status": "ok", "listing": ov, "rss": None, "final_url": mb}
        else:
            d = discover(mb, rendered)
        rep["municipal"] = {
            "base": mb,
            **{k: d[k] for k in ("status", "listing", "rss")},
        }
        if d["listing"] or d["rss"]:
            sources.append(make_src("municipal", c, d, mb))
    else:
        rep["municipal"] = {
            "base": None,
            "status": "no-wikidata",
            "listing": None,
            "rss": None,
        }
    pb = police_base(c)
    ov = LISTING_OVERRIDE.get((slug, "police"))
    if ov:
        d = {"status": "ok", "listing": ov, "rss": None, "final_url": pb}
    else:
        d = discover(pb, rendered)
    rep["police"] = {"base": pb, **{k: d[k] for k in ("status", "listing", "rss")}}
    if d["listing"] or d["rss"]:
        sources.append(make_src("police", c, d, pb))
    return {"city": c["name"], "sources": sources}, rep


def print_coverage(cities, registry, report):
    muni_ok = sum(
        1 for s in report.values() if s.get("municipal", {}).get("status") == "ok"
    )
    muni_any = sum(
        1
        for s in report.values()
        if s.get("municipal", {}).get("listing") or s.get("municipal", {}).get("rss")
    )
    pol_ok = sum(
        1 for s in report.values() if s.get("police", {}).get("status") == "ok"
    )
    pol_any = sum(
        1
        for s in report.values()
        if s.get("police", {}).get("listing") or s.get("police", {}).get("rss")
    )
    both = sum(1 for s in registry.values() if len(s["sources"]) >= 2)
    none = [slug for slug, s in registry.items() if not s["sources"]]
    print("\n" + "=" * 64)
    print(f"COVERAGE  ({len(cities)} cities)")
    print(f"  municipal: listing OK={muni_ok}  any(listing|rss)={muni_any}")
    print(f"  police:    listing OK={pol_ok}  any(listing|rss)={pol_any}")
    print(f"  cities with >=2 sources: {both}")
    print(f"  cities with 0 sources:   {len(none)} {none}")
    print("=" * 64)
    for label, kind in (("municipal", "municipal"), ("police", "police")):
        print(f"Gaps ({label} not ok):")
        for slug, s in report.items():
            x = s.get(kind, {})
            if x.get("status") != "ok":
                print(
                    f"  {slug:24} {label[:4]}={str(x.get('status')):12} base={x.get('base')}"
                )


def save(registry, report):
    json.dump(
        registry,
        open(ROOT / "sources.json", "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    json.dump(
        report,
        open(ROOT / "sources_report.json", "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )


async def main():
    global WD
    cities = CITIES
    qids = [c["wikidata"] for c in cities if c.get("wikidata")]
    print("Fetching Wikidata official websites ...")
    WD = wikidata_sites(qids)
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    # -------------------------------------------------- RETRY MODE
    if arg == "retry":
        registry = json.load(open(ROOT / "sources.json", encoding="utf-8"))
        report = json.load(open(ROOT / "sources_report.json", encoding="utf-8"))
        gap_slugs = [
            slug
            for slug, s in report.items()
            if s.get("municipal", {}).get("status") not in ("ok", "no-wikidata")
            or s.get("police", {}).get("status") != "ok"
        ]
        gap_cities = [c for c in cities if c["slug"] in gap_slugs]
        urls = set()
        for c in gap_cities:
            if report[c["slug"]].get("municipal", {}).get("status") not in (
                "ok",
                "no-wikidata",
            ):
                if municipal_base(c):
                    urls.add(municipal_base(c))
            if report[c["slug"]].get("police", {}).get("status") != "ok":
                urls.add(police_base(c))
        print(
            f"RETRY: {len(gap_cities)} gap cities, {len(urls)} URLs "
            f"(batch=6, networkidle, patient) ..."
        )
        rendered = await render_all(
            list(urls), batch=6, wait="networkidle", delay=2.5, timeout=60000
        )
        for c in gap_cities:
            new_entry, new_rep = assemble_city(c, rendered)
            old_rep = report[c["slug"]]
            # only overwrite a field if retry improved it (got listing/rss)
            for kind in ("municipal", "police"):
                if new_rep[kind].get("listing") or new_rep[kind].get("rss"):
                    old_rep[kind] = new_rep[kind]
            # rebuild sources from (possibly) updated report by re-running discover off merged status
            merged_sources = []
            for kind, base_fn in (
                ("municipal", municipal_base),
                ("police", police_base),
            ):
                r = old_rep.get(kind, {})
                if r.get("listing") or r.get("rss"):
                    d = {
                        "listing": r.get("listing"),
                        "rss": r.get("rss"),
                        "final_url": r.get("base"),
                    }
                    merged_sources.append(make_src(kind, c, d, r.get("base")))
            registry[c["slug"]] = {"city": c["name"], "sources": merged_sources}
        save(registry, report)
        print_coverage(cities, registry, report)
        print(f"\n-> updated sources.json, sources_report.json")
        return

    # -------------------------------------------------- FULL BUILD
    limit = int(arg) if arg.isdigit() else len(cities)
    cities = CITIES[:limit]
    print(f"Cities to process: {len(cities)}")
    urls = set()
    for c in cities:
        if municipal_base(c):
            urls.add(municipal_base(c))
        urls.add(police_base(c))
    print(f"Rendering {len(urls)} unique base URLs (municipal + police) ...")
    rendered = await render_all(list(urls))

    registry, report = {}, {}
    for c in cities:
        registry[c["slug"]], report[c["slug"]] = assemble_city(c, rendered)
    save(registry, report)
    print_coverage(cities, registry, report)
    print(f"\n-> sources.json, sources_report.json written to {ROOT}")


if __name__ == "__main__":
    asyncio.run(main())
