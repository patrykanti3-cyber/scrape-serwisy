"""Resilient body extraction: try a list of CSS selectors (in order) against the
rendered HTML until the cleaned body clears the Quality Gate.

Purpose: a single portal changing one CSS class must NOT take a whole city down.
`content_selector` is tried first, then `fallback_selectors` from sources.json,
then a set of generic defaults. Pure + offline → unit-tested without a browser.
"""
from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag

from cleaners import clean_municipal_body

# Generic, site-agnostic fallbacks tried after the per-source ones. Ordered from
# most-specific/likely to broadest.
DEFAULT_FALLBACK_SELECTORS = [
    "article", "main", ".article-body", ".article__body", ".news-body",
    ".entry-content", ".post-content", ".content", "#content", "#content-area",
    "div.tresc", ".tresc", "[itemprop='articleBody']",
]

_DROP_TAGS = ("script", "style", "nav", "footer", "aside", "form", "noscript")


def element_to_markdown(node: Tag) -> str:
    """Compact HTML→markdown for a content subtree: headings, paragraphs, list
    items, quotes and images. Good enough for the Quality Gate + the rewrite LLM.
    """
    for junk in node.find_all(_DROP_TAGS):
        junk.decompose()
    blocks: list[str] = []
    for el in node.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "img"]):
        if el.name == "img":
            src = el.get("src")
            if src:
                blocks.append(f"![{el.get('alt', '')}]({src})")
            continue
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name and el.name.startswith("h"):
            blocks.append(f"## {txt}")
        elif el.name == "li":
            blocks.append(f"- {txt}")
        elif el.name == "blockquote":
            blocks.append(f"> {txt}")
        else:
            blocks.append(txt)
    if not blocks:  # no block-level structure — fall back to the raw text
        txt = node.get_text(" ", strip=True)
        if txt:
            blocks.append(txt)
    return "\n\n".join(blocks)


def candidate_selectors(content_selector, fallback_selectors):
    """Ordered, de-duplicated selector list: primary, per-source fallbacks, then
    generic defaults."""
    out: list[str] = []
    for s in [content_selector, *(fallback_selectors or []), *DEFAULT_FALLBACK_SELECTORS]:
        if s and s not in out:
            out.append(s)
    return out


def extract_body_with_fallbacks(html, selectors, min_len, end_markers=None,
                                noise_patterns=None) -> dict:
    """Try each selector in order; return the first whose cleaned body length
    >= min_len. If none pass, return the longest body found (best-effort).

    Returns {"title","lead","body","selector"} — selector is None if nothing
    matched at all.
    """
    best = {"title": "", "lead": "", "body": "", "selector": None}
    tried: set[str] = set()
    for sel in selectors:
        if not sel or sel in tried:
            continue
        tried.add(sel)
        soup = BeautifulSoup(html or "", "html.parser")
        try:
            nodes = soup.select(sel)
        except Exception:  # invalid selector — skip, don't crash the city
            continue
        if not nodes:
            continue
        md = "\n\n".join(element_to_markdown(n) for n in nodes)
        title, lead, body = clean_municipal_body(
            md, end_markers=end_markers, noise_patterns=noise_patterns)
        if len(body) >= min_len:
            return {"title": title, "lead": lead, "body": body, "selector": sel}
        if len(body) > len(best["body"]):
            best = {"title": title, "lead": lead, "body": body, "selector": sel}
    return best


def pagination_links(html, page_url, base, selector) -> list[str]:
    """Next-page URLs from a listing page, found via `pagination_selector`.

    Urzędy paginate their news/announcement listings; following the pager lets
    us reach more than the first page of articles. Returns same-host URLs in
    document order (de-duplicated). Never raises on a bad selector."""
    from urllib.parse import urljoin
    from discovery import same_host

    soup = BeautifulSoup(html or "", "html.parser")
    try:
        nodes = soup.select(selector)
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        anchors = [node] if node.name == "a" else node.find_all("a")
        for a in anchors:
            href = a.get("href")
            if not href or href.startswith("#"):
                continue
            full = urljoin(page_url, href)
            if same_host(full, base) and full not in seen:
                seen.add(full)
                out.append(full)
    return out
