"""Integration test for the events track: scrape_events must attach a structured
date/time/location anchor to EventItem.lead (deterministic, no browser/network)."""
import sys
import pathlib
import asyncio

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

import scraper


class FakeResult:
    def __init__(self, url, html="", markdown="", links=None, success=True):
        self.url = url
        self.html = html
        self.markdown = markdown
        self.links = links or {}
        self.metadata = {}
        self.success = success
        self.error_message = ""


class FakeRun:
    def clone(self, **kwargs):
        return self


class FakeCrawler:
    def __init__(self, pages):
        self.pages = pages

    async def arun_many(self, urls, config=None):
        return [self.pages.get(u) or FakeResult(u, success=False) for u in urls]


BASE = "https://um.example.pl"
ART = f"{BASE}/wydarzenia/koncert-plenerowy-maj"

LISTING = FakeResult(
    f"{BASE}/wydarzenia",
    html="<html><body><a href='/wydarzenia/koncert-plenerowy-maj'>x</a></body></html>",
    links={"internal": [{"href": "/wydarzenia/koncert-plenerowy-maj"}]},
)

ARTICLE = FakeResult(
    ART,
    html='<meta property="og:image" content="https://um.example.pl/foto.jpg">',
    markdown=(
        "# Koncert plenerowy w mieście\n\n"
        "Zapraszamy na koncert plenerowy dla mieszkańców, wstęp wolny. "
        "Data: 12.05.2026, godz. 18:00. Miejsce: Amfiteatr miejski.\n\n"
        "Wydarzenie potrwa około dwóch godzin i zakończy się pokazem świateł."
    ),
)


def test_scrape_events_attaches_structured_lead():
    src = {
        "type": "events_html", "name": "Wydarzenia — Example", "credit": "UM Example",
        "base": BASE, "listing": [f"{BASE}/wydarzenia"], "article_marker": "/wydarzenia/",
        "content_selector": "main",
    }
    crawler = FakeCrawler({LISTING.url: LISTING, ART: ARTICLE})
    events = asyncio.run(scraper.scrape_events(crawler, FakeRun(), "example", src, 5))

    assert len(events) == 1, events
    ev = events[0]
    assert ev.lead == "Data: 12.05.2026, Godzina: 18:00, Miejsce: Amfiteatr miejski"
    # date_iso derived from the extracted date -> reliable publish timestamp
    assert ev.published == "2026-05-12"
    assert ev.title == "Koncert plenerowy w mieście"
