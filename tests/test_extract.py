import sys
import pathlib

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

from extract import (
    candidate_selectors,
    element_to_markdown,
    extract_body_with_fallbacks,
    DEFAULT_FALLBACK_SELECTORS,
)
from bs4 import BeautifulSoup

LONG = "Treść artykułu o remoncie ulicy w mieście, wystarczająco długa. " * 6  # >250 chars

HTML = f"""
<html><body>
  <nav>Menu Aktualności Kontakt</nav>
  <article class="news-body">
    <h1>Remont ulicy Długiej</h1>
    <p>{LONG}</p>
    <ul><li>Objazd ulicą Krótką</li></ul>
    <script>tracking()</script>
  </article>
  <footer>Stopka urzędu</footer>
</body></html>
"""


def test_candidate_selectors_order_and_dedup():
    sels = candidate_selectors("article.main-content", [".news-body", "article"])
    assert sels[0] == "article.main-content"
    assert sels[1] == ".news-body"
    # "article" appears once even though it's also a default
    assert sels.count("article") == 1
    # defaults appended
    assert ".content" in sels


def test_element_to_markdown_strips_chrome_keeps_structure():
    node = BeautifulSoup(HTML, "html.parser").select_one("article")
    md = element_to_markdown(node)
    assert "## Remont ulicy Długiej" in md
    assert "- Objazd ulicą Krótką" in md
    assert "tracking()" not in md  # script dropped


def test_fallback_used_when_primary_selector_missing():
    # primary selector doesn't exist; a fallback (.news-body) does -> body passes.
    res = extract_body_with_fallbacks(
        HTML, candidate_selectors("article.main-content", [".news-body"]), min_len=250)
    assert res["selector"] == ".news-body"
    assert len(res["body"]) >= 250
    assert res["title"] == "Remont ulicy Długiej"


def test_primary_selector_wins_when_present():
    res = extract_body_with_fallbacks(
        HTML, candidate_selectors("article", []), min_len=250)
    assert res["selector"] == "article"
    assert len(res["body"]) >= 250


def test_no_match_returns_empty_best_effort():
    res = extract_body_with_fallbacks(
        "<html><body><div class='x'>tiny</div></body></html>",
        candidate_selectors(".does-not-exist", []), min_len=250)
    # generic defaults may match nothing meaningful -> body below gate, selector
    # is either None or a matched-but-short one; never raises.
    assert res["body"] == "" or len(res["body"]) < 250


def test_invalid_selector_does_not_crash():
    res = extract_body_with_fallbacks(HTML, ["!!!bad>>", ".news-body"], min_len=250)
    assert res["selector"] == ".news-body"


def test_pagination_links_follows_pager():
    from extract import pagination_links
    html = """
    <div class="pager">
      <a href="/aktualnosci?page=1">1</a>
      <a href="/aktualnosci?page=2">2</a>
      <a href="#">#</a>
      <a href="https://inny.pl/x">obcy</a>
    </div>
    """
    base = "https://um.example.pl"
    urls = pagination_links(html, base + "/aktualnosci", base, ".pager")
    assert urls == [
        "https://um.example.pl/aktualnosci?page=1",
        "https://um.example.pl/aktualnosci?page=2",
    ]


def test_pagination_links_bad_selector_empty():
    from extract import pagination_links
    assert pagination_links("<div/>", "https://x.pl", "https://x.pl", ">>bad") == []
