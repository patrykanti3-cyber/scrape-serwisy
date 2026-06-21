import sys
import pathlib
import pytest

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

from cleaners import (
    clean,
    find_date_iso,
    strip_md_chrome,
    clean_police_body,
    clean_municipal_body,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_clean_municipal_body_real_sample():
    md = (PROJECT_ROOT / "slupsk_art_0.md").read_text(encoding="utf-8")
    title, lead, body = clean_municipal_body(md)
    assert title == "Apteki całodobowe i dyżurne w Słupsku gdzie kupić leki w nocy i w niedzielę?"
    assert "Podziel się" not in body
    assert "Nadchodzące wydarzenia" not in body
    assert "| pn. | wt. |" not in body
    assert "**" not in lead
    assert body


def test_clean_police_body_bold_lead():
    md = "# Title\n**Lead sentence here.** Body paragraph follows with enough text to be meaningful."
    lead, body = clean_police_body(md)
    assert lead == "Lead sentence here."
    assert "Body paragraph follows" in body
    assert "**" not in body


def test_strip_md_chrome_preserves_images_strips_links():
    out = strip_md_chrome("![alt](http://img.png) [link text](http://link.com) plain")
    assert "![alt](http://img.png)" in out
    assert "[link text]" not in out
    assert "link text" in out


def test_strip_md_chrome_preserves_inline_images():
    md = "before ![first](http://a/1.png) middle ![second](http://b/2.jpg) after"
    out = strip_md_chrome(md)
    assert "![first](http://a/1.png)" in out
    assert "![second](http://b/2.jpg)" in out
    stripped = out.replace("![first](http://a/1.png)", "").replace("![second](http://b/2.jpg)", "")
    assert " ".join(stripped.split()) == "before middle after"


def test_find_date_iso_numeric():
    assert find_date_iso("02.06.2026") == "2026-06-02"


def test_find_date_iso_polish_month_returns_empty():
    assert find_date_iso("15 czerwca 2026") == ""


def test_clean_municipal_body_custom_end_markers():
    md = "# My Title\nFirst line of content.\nSTOPHERE\nThis should be cut."
    title, lead, body = clean_municipal_body(md, end_markers=r"^STOPHERE$")
    assert title == "My Title"
    assert "First line of content" in body
    assert "This should be cut" not in body


def test_clean_whitespace_normalizer():
    assert clean("a   b\t\tc\n\n\n\nd") == "a b c\n\nd"
    assert clean("  trim me  ") == "trim me"
    assert clean("") == ""
