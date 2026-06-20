"""Analyze where 24slupsk.pl 'Wiadomości' content is sourced from.
Collects news article URLs, crawls them, dumps a few full bodies, and extracts
every footprint + inline references to official sources (urzędy, policja, BIP...).
"""
import asyncio, json, re, sys
from urllib.parse import urljoin, urlparse
from collections import Counter
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode

sys.stdout.reconfigure(encoding="utf-8")
DOMAIN = "24slupsk.pl"
SEEDS = [
    "https://24slupsk.pl/kategorie/news",
    "https://24slupsk.pl/kategorie/ciekawe",
    "https://24slupsk.pl/",
]
MAX_ART = int(sys.argv[1]) if len(sys.argv) > 1 else 30

ART_RE = re.compile(r"^/\d{6,}/")   # path like /20260617298331/slug

# footprint
AUTHOR_RE      = re.compile(r"Autor:\s*([^\n\[]+)")
ZRODLO_LINK_RE = re.compile(r"Materia[łl] [źz]r[óo]d[łl]owy:\s*\[([^\]]+)\]\(([^)]+)\)", re.IGNORECASE)
NAPOD_RE       = re.compile(r"na podstawie\s*:?\s*([^\n]{0,120})", re.IGNORECASE)
ILU_RE         = re.compile(r"Ilustracja wykorzystana[^\n]*?zewn[ęe]trznego [źz]r[óo]d[łl]a\s*\(([^)]*)\)", re.IGNORECASE)

# inline official-source mentions in body
OFFICIAL = {
    "Urząd Miasta / UM":  re.compile(r"\b(Urz[ąa]d(?:u|em)? Miasta|Urz[ąa]d Miejski|magistrat|ratusz)\b", re.IGNORECASE),
    "Starostwo/Powiat":   re.compile(r"\b(Starostw\w*|Urz[ąa]d Powiatow\w*|powiat\w*)\b", re.IGNORECASE),
    "Policja (KPP/KMP)":  re.compile(r"\b(policj\w*|KPP|KMP|komend\w* policji|rzecznik\w* (?:KPP|KMP|policji))\b", re.IGNORECASE),
    "Straż Pożarna/PSP":  re.compile(r"\b(stra[żz]\w* po[żz]arn\w*|PSP|JRG|KM PSP|KP PSP)\b", re.IGNORECASE),
    "Straż Miejska":      re.compile(r"\bstra[żz]\w* miejsk\w*\b", re.IGNORECASE),
    "Urząd Marszałk./Woj.":re.compile(r"\b(Urz[ąa]d Marsza[łl]kowski|Urz[ąa]d Wojew[óo]dzki|wojewoda|marsza[łl]ek)\b", re.IGNORECASE),
    "GDDKiA/drogi":       re.compile(r"\b(GDDKiA|Zarz[ąa]d Dr[óo]g|ZDW|ZDP|drogowcy)\b", re.IGNORECASE),
    "ZUS/KRUS":           re.compile(r"\b(ZUS|Zak[łl]ad Ubezpiecze[ńn]|KRUS)\b"),
    "GUS/dane":           re.compile(r"\b(GUS|G[łl][óo]wny Urz[ąa]d Statystyczny)\b"),
    "Sanepid/GIS":        re.compile(r"\b(Sanepid|Stacj\w* Sanitarn\w*|GIS|Inspekcj\w* Sanitarn\w*)\b", re.IGNORECASE),
    "IMGW/pogoda":        re.compile(r"\b(IMGW|Instytut Meteorologii)\b"),
    "Sąd/Prokuratura":    re.compile(r"\b(s[ąa]d\w*|prokuratur\w*|prokurator)\b", re.IGNORECASE),
    "rzecznik prasowy":   re.compile(r"\brzecznik\w*\b", re.IGNORECASE),
    "komunikat/informuje":re.compile(r"\b(komunikat\w*|jak (?:informuje|przekazuje|podaje)|poinformowa\w*)\b", re.IGNORECASE),
    "BIP/gov.pl":         re.compile(r"\b(bip\.|gov\.pl|biuletyn informacji publicznej)\b", re.IGNORECASE),
}

def md_of(r):
    m = r.markdown
    return m.raw_markdown if hasattr(m, "raw_markdown") else str(m)

async def main():
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000, wait_until="domcontentloaded")
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as c:
        # collect article urls
        arts = []
        seen = set()
        seedres = await c.arun_many(SEEDS, config=run)
        for r in seedres:
            if not r.success: continue
            for l in (r.links.get("internal", []) if r.links else []):
                h = l.get("href") if isinstance(l, dict) else l
                if not h: continue
                full = urljoin(r.url, h)
                p = urlparse(full)
                if p.netloc.replace("www.","") != DOMAIN: continue
                if ART_RE.match(p.path) and full not in seen:
                    seen.add(full); arts.append(full)
        arts = arts[:MAX_ART]
        print(f"Collected {len(arts)} news article URLs", flush=True)

        results = await c.arun_many(arts, config=run)
        recs = []
        official_counter = Counter()
        for i, r in enumerate(results):
            if not r.success: continue
            md = md_of(r)
            if i < 4:
                open(f"slupsk_art_{i}.md", "w", encoding="utf-8").write(md)
            rec = {"url": r.url}
            m = AUTHOR_RE.search(md); rec["author"] = m.group(1).strip() if m else None
            m = ZRODLO_LINK_RE.search(md)
            if m:
                rec["source_url"] = m.group(2).strip()
                rec["source_domain"] = urlparse(m.group(2)).netloc.replace("www.","").lower()
            m = NAPOD_RE.search(md); rec["na_podstawie"] = m.group(1).strip() if m else None
            m = ILU_RE.search(md); rec["illustration_source"] = (m.group(1).strip() or "(puste)") if m else None
            present = [name for name, rx in OFFICIAL.items() if rx.search(md)]
            for nm in present: official_counter[nm] += 1
            rec["official_mentions"] = present
            recs.append(rec)

        json.dump(recs, open("slupsk_news.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        print("\n=== AUTORZY ===")
        for v,cnt in Counter(r["author"] for r in recs if r.get("author")).most_common():
            print(f"  {cnt:3d}  {v}")
        print("\n=== MATERIAŁ ŹRÓDŁOWY (domena) ===")
        for v,cnt in Counter(r["source_domain"] for r in recs if r.get("source_domain")).most_common():
            print(f"  {cnt:3d}  {v}")
        print("\n=== 'NA PODSTAWIE' ===")
        for r in recs:
            if r.get("na_podstawie"): print("  -", r["na_podstawie"])
        print("\n=== ILUSTRACJA ===")
        for r in recs:
            if r.get("illustration_source"): print("  -", r["illustration_source"])
        print("\n=== ODWOŁANIA DO ŹRÓDEŁ OFICJALNYCH (w treści) — ile artykułów ===")
        for v,cnt in official_counter.most_common():
            print(f"  {cnt:3d}/{len(recs)}  {v}")

asyncio.run(main())
