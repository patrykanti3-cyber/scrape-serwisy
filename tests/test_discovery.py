import sys
import pathlib

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

from discovery import (
    same_host,
    anchor_hrefs,
    filter_article_urls,
    robots_sitemaps,
    head_rss_link,
    parse_sitemap_urls,
    preflight_discover,
)


# --------------------------------------------------------------- link extraction
def test_anchor_hrefs_extracts_and_skips_anchors_only():
    html = (
        '<a href="/aktualnosci/nowy-most-otwarty">x</a>'
        "<a href='#top'>skip</a>"
        '<a href="https://um.example.pl/aktualnosci/druga-wiadomosc-dnia">y</a>'
        '<a>no href</a>'
    )
    hrefs = anchor_hrefs(html)
    assert "/aktualnosci/nowy-most-otwarty" in hrefs
    assert "https://um.example.pl/aktualnosci/druga-wiadomosc-dnia" in hrefs
    assert "#top" not in hrefs  # pure-anchor links are skipped


def test_filter_article_urls_same_host_marker_and_slug():
    base = "https://um.example.pl"
    hrefs = [
        "/aktualnosci/nowy-most-otwarty-dzis",   # ok: marker + 3 dashes
        "/aktualnosci/krotki",                    # drop: <2 dashes
        "/kontakt",                               # drop: no marker
        "https://inny.pl/aktualnosci/obcy-host-artykul",  # drop: other host
        "https://www.um.example.pl/aktualnosci/druga-wazna-wiadomosc",  # ok (www)
    ]
    urls = filter_article_urls(hrefs, page_url=base + "/aktualnosci", base=base,
                               marker="/aktualnosci/")
    assert urls == [
        "https://um.example.pl/aktualnosci/nowy-most-otwarty-dzis",
        "https://www.um.example.pl/aktualnosci/druga-wazna-wiadomosc",
    ]


def test_filter_article_urls_dedupes():
    base = "https://um.example.pl"
    hrefs = ["/aktualnosci/ten-sam-artykul", "/aktualnosci/ten-sam-artykul"]
    urls = filter_article_urls(hrefs, base, base, "/aktualnosci/")
    assert len(urls) == 1


def test_filter_article_urls_marker_agnostic():
    """Empty marker => relaxed pass: same-host + article-like slug (>=2 dashes),
    used as the SPA fallback when a configured marker is stale."""
    base = "https://um.example.pl"
    hrefs = [
        "/-/nowa-inwestycja-w-centrum",   # ok: 3 dashes, no /aktualnosci/ marker
        "/kontakt",                        # drop: single segment, 0 dashes
        "/dla-mieszkancow",                # drop: 1 dash (<2)
    ]
    assert filter_article_urls(hrefs, base, base, marker="/aktualnosci/") == []
    assert filter_article_urls(hrefs, base, base, marker="") == [
        "https://um.example.pl/-/nowa-inwestycja-w-centrum",
    ]


def test_same_host_ignores_www_scheme_port():
    assert same_host("https://www.um.example.pl:443/x", "http://um.example.pl")
    assert not same_host("https://a.pl", "https://b.pl")


# --------------------------------------------------------------- RSS / sitemap
def test_robots_sitemaps_parses_lines():
    robots = "User-agent: *\nDisallow: /admin\nSitemap: https://x.pl/sitemap.xml\nsitemap: https://x.pl/news.xml\n"
    assert robots_sitemaps(robots) == ["https://x.pl/sitemap.xml", "https://x.pl/news.xml"]


def test_head_rss_link_prefers_news_title():
    html = (
        '<link rel="alternate" type="application/rss+xml" title="Komentarze" href="/comments.xml">'
        '<link rel="alternate" type="application/rss+xml" title="Aktualności" href="/news.xml">'
    )
    assert head_rss_link(html, "https://um.example.pl") == "https://um.example.pl/news.xml"


def test_head_rss_link_none_when_absent():
    assert head_rss_link("<link rel=stylesheet href=/a.css>", "https://x.pl") is None


def test_parse_sitemap_urls_namespaced():
    xml = (
        '<?xml version="1.0"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://x.pl/aktualnosci/a-b-c</loc></url>"
        "<url><loc>https://x.pl/aktualnosci/d-e-f</loc></url>"
        "</urlset>"
    )
    assert parse_sitemap_urls(xml) == [
        "https://x.pl/aktualnosci/a-b-c",
        "https://x.pl/aktualnosci/d-e-f",
    ]


def test_parse_sitemap_urls_bad_xml_is_empty():
    assert parse_sitemap_urls("<not xml") == []


# --------------------------------------------------------------- preflight (injected fetch)
def test_preflight_discover_finds_rss_and_sitemap():
    pages = {
        "https://um.example.pl/robots.txt": b"Sitemap: https://um.example.pl/sitemap.xml\n",
        "https://um.example.pl/": b'<link rel="alternate" type="application/rss+xml" title="Aktualnosci" href="/rss.xml">',
    }
    def fake_fetch(url):
        return pages[url]
    out = preflight_discover("https://um.example.pl", fake_fetch)
    assert out["rss"] == "https://um.example.pl/rss.xml"
    assert out["sitemaps"] == ["https://um.example.pl/sitemap.xml"]


def test_preflight_discover_falls_back_to_default_sitemap():
    def fake_fetch(url):
        raise RuntimeError("network down")
    out = preflight_discover("https://um.example.pl", fake_fetch)
    assert out["rss"] is None
    assert out["sitemaps"] == ["https://um.example.pl/sitemap.xml"]
