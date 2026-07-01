"""Update sources.json: convert cities with municipal RSS feeds from
municipal_html to municipal_rss, using the RSS URLs from rss_audit.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sources = json.load(open(ROOT / "srcfeeds" / "sources.json", encoding="utf-8"))
audit = json.load(open(ROOT / "srcfeeds" / "rss_audit.json", encoding="utf-8"))

updated = 0
for slug, info in audit.items():
    rss_url = info.get("rss")
    if not rss_url or slug not in sources:
        continue
    if "policja.gov.pl" in rss_url:
        continue
    for src in sources[slug]["sources"]:
        if src["type"] == "municipal_html" and "urząd" in src["name"].lower():
            src["type"] = "municipal_rss"
            src["feed"] = rss_url
            src.pop("listing", None)
            src.pop("article_marker", None)
            updated += 1
            break

with open(ROOT / "srcfeeds" / "sources.json", "w", encoding="utf-8") as f:
    json.dump(sources, f, ensure_ascii=False, indent=2)

print(f"Updated {updated} cities from municipal_html to municipal_rss")
