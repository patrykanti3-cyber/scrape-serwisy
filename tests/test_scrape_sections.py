"""Integration test for the new section-based sources.json structure:
scrape_municipal must gather links per section container and tag each article
with that section's category_hint (deterministic, no browser/network)."""
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


BASE = "https://slupsk.pl"
OG = '<meta property="og:image" content="https://slupsk.pl/foto.jpg">'
BODY = "Treść artykułu wystarczająco długa, by przejść bramkę jakości. " * 6  # >250

HOME = FakeResult(
    f"{BASE}/",
    html='<section class="wiadomosci"><a href="/artykul/wazna-wiadomosc-dnia">A</a></section>'
         '<section class="na-sygnale"><a href="/artykul/nocna-interwencja-strazy">B</a></section>',
)
SPORT = FakeResult(
    f"{BASE}/sport",
    html='<section class="sport"><a href="/artykul/bieg-uliczny-w-slupsku">C</a></section>',
)
ART = {
    f"{BASE}/artykul/wazna-wiadomosc-dnia": FakeResult(
        f"{BASE}/artykul/wazna-wiadomosc-dnia", html=OG,
        markdown=f"# Ważna wiadomość dnia\n\n{BODY}"),
    f"{BASE}/artykul/nocna-interwencja-strazy": FakeResult(
        f"{BASE}/artykul/nocna-interwencja-strazy", html=OG,
        markdown=f"# Nocna interwencja straży\n\n{BODY}"),
    f"{BASE}/artykul/bieg-uliczny-w-slupsku": FakeResult(
        f"{BASE}/artykul/bieg-uliczny-w-slupsku", html=OG,
        markdown=f"# Bieg uliczny w Słupsku\n\n{BODY}"),
}

SRC = {
    "type": "municipal_html", "name": "Urząd — Słupsk", "credit": "UM Słupsk",
    "base": BASE,
    "sections": [
        {"name": "Najważniejsze wiadomości", "url": "/",
         "container_selector": "section.wiadomosci a", "category_hint": "wiadomosci"},
        {"name": "Na sygnale", "url": "/",
         "container_selector": "section.na-sygnale a", "category_hint": "na-sygnale"},
        {"name": "Sport", "url": "/sport",
         "container_selector": "section.sport a", "category_hint": "sport"},
    ],
    "article_marker": "div.article-content",
    "content_selector": "div.article-body",
}


def test_sections_gather_and_tag_category():
    crawler = FakeCrawler({HOME.url: HOME, SPORT.url: SPORT, **ART})
    items = asyncio.run(scraper.scrape_municipal(crawler, FakeRun(), "slupsk", SRC, 10))

    by_slug = {it.source_url.rsplit("/", 1)[-1]: it for it in items}
    assert set(by_slug) == {
        "wazna-wiadomosc-dnia", "nocna-interwencja-strazy", "bieg-uliczny-w-slupsku"
    }, by_slug
    assert by_slug["wazna-wiadomosc-dnia"].category_hint == "wiadomosci"
    assert by_slug["nocna-interwencja-strazy"].category_hint == "na-sygnale"
    assert by_slug["bieg-uliczny-w-slupsku"].category_hint == "sport"
    # bodies extracted (all cleared the gate)
    assert all(len(it.body) > 250 for it in items)


def test_legacy_listing_still_works(monkeypatch):
    """Backward compat: a flat `listing` source (no sections) keeps working and
    tags articles with the source-level category."""
    monkeypatch.setattr(scraper, "http_get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))
    LIST = FakeResult(
        f"{BASE}/aktualnosci",
        html='<a href="/aktualnosci/nowy-most-w-slupsku">x</a>',
        links={"internal": [{"href": "/aktualnosci/nowy-most-w-slupsku"}]},
    )
    art = FakeResult(f"{BASE}/aktualnosci/nowy-most-w-slupsku", html=OG,
                     markdown=f"# Nowy most w Słupsku\n\n{BODY}")
    src = {
        "type": "municipal_html", "name": "Urząd — X", "credit": "UM X", "base": BASE,
        "listing": [f"{BASE}/aktualnosci"], "article_marker": "/aktualnosci/",
        "content_selector": "main", "category": "sport",
    }
    crawler = FakeCrawler({LIST.url: LIST, art.url: art})
    items = asyncio.run(scraper.scrape_municipal(crawler, FakeRun(), "x", src, 10))
    assert len(items) == 1
    assert items[0].category_hint == "sport"
    assert len(items[0].body) > 250
