"""Analyze results.jsonl from scrape_sources.py and write a UTF-8 report."""
import json, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent
CITIES = json.load(open(r"C:\Users\User\polska-portal\data\cities.json", encoding="utf-8"))
NETWORK = {c["domain"].lower() for c in CITIES}

rows = [json.loads(l) for l in open(ROOT / "results.jsonl", encoding="utf-8") if l.strip()]
ok = [r for r in rows if r.get("ok")]

n = len(ok)
has_author   = [r for r in ok if r.get("author")]
has_srcurl   = [r for r in ok if r.get("source_url")]
has_srctxt   = [r for r in ok if r.get("source_text") and not r.get("source_url")]
has_napod    = [r for r in ok if r.get("na_podstawie")]
has_ilu      = [r for r in ok if r.get("illustration_source")]

out = []
def p(*a):
    s = " ".join(str(x) for x in a)
    out.append(s); print(s)

p("=" * 70)
p("ANALIZA ŹRÓDEŁ ARTYKUŁÓW — sieć serwisylokalne.pl")
p("=" * 70)
p(f"Portali w sieci (cities.json): {len(NETWORK)}")
p(f"Pobranych artykułów (ok): {n} / {len(rows)}")
p("")
p("WYSTĘPOWANIE ELEMENTÓW STOPKI:")
p(f"  Autor:                         {len(has_author):4d}  ({len(has_author)/n*100:.1f}%)")
p(f"  Materiał źródłowy (z linkiem): {len(has_srcurl):4d}  ({len(has_srcurl)/n*100:.1f}%)")
p(f"  Materiał źródłowy (tekst):     {len(has_srctxt):4d}  ({len(has_srctxt)/n*100:.1f}%)")
p(f"  'na podstawie:'                {len(has_napod):4d}  ({len(has_napod)/n*100:.1f}%)")
p(f"  Ilustracja z zewn. źródła:     {len(has_ilu):4d}  ({len(has_ilu)/n*100:.1f}%)")

# ---- source-domain topology ----
src_dom = Counter(r["source_domain"] for r in has_srcurl)
in_net  = sum(c for d, c in src_dom.items() if d in NETWORK)
out_net = sum(c for d, c in src_dom.items() if d not in NETWORK)
p("")
p("DOMENY 'MATERIAŁ ŹRÓDŁOWY' (skąd republikowane treści):")
p(f"  Łącznie z linkiem źródłowym: {len(has_srcurl)}")
p(f"  Wskazują portal z TEJ SAMEJ sieci: {in_net}")
p(f"  Wskazują domenę spoza sieci:       {out_net}")
p("  TOP domeny źródłowe:")
for d, c in src_dom.most_common(20):
    tag = "  [w sieci]" if d in NETWORK else "  [SPOZA]"
    p(f"    {c:4d}  {d}{tag}")

# self vs cross
self_src = sum(1 for r in has_srcurl if r["source_domain"] == r["domain"])
cross    = len(has_srcurl) - self_src
p("")
p(f"  Źródło = ten sam portal: {self_src}")
p(f"  Źródło = inny portal:    {cross}")

# ---- na podstawie ----
p("")
p("'NA PODSTAWIE:' — wartości (TOP 20):")
for v, c in Counter(r["na_podstawie"][:80] for r in has_napod).most_common(20):
    p(f"    {c:4d}  {v}")

# ---- illustration ----
p("")
p("ILUSTRACJA — źródło (TOP 20):")
for v, c in Counter((r["illustration_source"] or "")[:80] for r in has_ilu).most_common(20):
    p(f"    {c:4d}  {v}")

# ---- authors ----
p("")
p("AUTORZY (TOP 25) — powtarzalność kont redakcyjnych w sieci:")
auth = Counter(r["author"].strip() for r in has_author)
for v, c in auth.most_common(25):
    p(f"    {c:4d}  {v}")
p(f"  Unikalnych nazw autorów: {len(auth)}")

# ---- per-portal coverage ----
by_portal = defaultdict(lambda: [0, 0])
for r in ok:
    by_portal[r["domain"]][0] += 1
    if r.get("source_url") or r.get("na_podstawie"):
        by_portal[r["domain"]][1] += 1
p("")
p(f"Portali z co najmniej 1 pobranym artykułem: {len(by_portal)}")

Path(ROOT / "report.txt").write_text("\n".join(out), encoding="utf-8")
print("\n[report.txt written]")
