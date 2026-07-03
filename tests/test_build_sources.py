import sys
import pathlib

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

import build_sources as B


def test_build_sections_main_plus_categories():
    d = {
        "listing": "https://x.pl/aktualnosci",
        "sections": {"sport": "https://x.pl/sport", "kultura": "https://x.pl/kultura",
                     "_events": "https://x.pl/wydarzenia"},
    }
    secs = B.build_sections(d)
    hints = {(s["category_hint"], s["url"]) for s in secs}
    assert ("wiadomosci", "https://x.pl/aktualnosci") in hints
    assert ("sport", "https://x.pl/sport") in hints
    assert ("kultura", "https://x.pl/kultura") in hints
    # events NOT turned into a section here (separate events_html source)
    assert all(s["category_hint"] != "_events" for s in secs)
    # every section is well-formed
    assert all(s.get("name") and s.get("url") and s.get("container_selector") for s in secs)


def test_build_sections_skips_duplicate_of_listing():
    d = {"listing": "https://x.pl/aktualnosci",
         "sections": {"sport": "https://x.pl/aktualnosci"}}  # same as listing
    secs = B.build_sections(d)
    assert len(secs) == 1 and secs[0]["category_hint"] == "wiadomosci"


def test_build_sections_empty_without_listing_or_cats():
    assert B.build_sections({"listing": None, "sections": {}}) == []


def test_make_municipal_sections_src_shape():
    c = {"name": "Testowo", "slug": "testowo"}
    d = {"listing": "https://x.pl/aktualnosci", "sections": {"sport": "https://x.pl/sport"},
         "final_url": "https://x.pl", "rss": "https://x.pl/rss"}
    src = B.make_municipal_sections_src(c, d, "https://x.pl")
    assert src["type"] == "municipal_html"
    assert isinstance(src["sections"], list) and len(src["sections"]) == 2
    assert src["article_marker"] == "/aktualnosci/"        # URL marker -> scraper filters
    assert src["content_selector"] == "main"
    assert src["rss"] == "https://x.pl/rss"
    assert {s["category_hint"] for s in src["sections"]} == {"wiadomosci", "sport"}
