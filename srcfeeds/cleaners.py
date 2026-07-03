"""Text cleaners for srcfeeds — markdown chrome stripping, body extraction,
and date parsing. Extracted from scraper.py so they can be unit-tested."""
from __future__ import annotations
import re
from datetime import datetime, timezone


def clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_md_chrome(md: str) -> str:
    images = []
    def _save_img(m):
        images.append(m.group(0))
        return f"\x00IMG{len(images)-1}\x00"
    md = re.sub(r"!\[[^\]]*\]\([^)]+\)", _save_img, md)

    md = re.sub(r"\[([^\]]*)\]\((?:javascript:|#)[^)]*\)", r"\1", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"javascript:[^\s)]*", "", md)
    md = re.sub(r'"\s*"?\)', " ", md)
    md = md.replace('")', " ").replace("()", " ")
    md = re.sub(r"\bDrukuj\b|\bPowrót\b|\bUdostępnij\b|Link skopiowany[^\n]*", "", md, flags=re.IGNORECASE)

    md = re.sub(r"\x00IMG(\d+)\x00", lambda m: images[int(m.group(1))], md)
    return md


def clean_police_body(md: str) -> tuple[str, str]:
    md = strip_md_chrome(md)
    md = re.sub(r"Data publikacji\s*[\d.\s]+", "", md)
    lines = []
    for l in md.splitlines():
        s = l.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    text = clean("\n".join(lines))
    mlead = re.search(r"\*\*(.+?)\*\*", text, re.DOTALL)
    lead = clean(re.sub(r"\*+", "", mlead.group(1))) if mlead else (text.split("\n")[0] if text else "")
    body = clean(re.sub(r"\*+", "", text))
    body = re.sub(r'^[\s"”“).\-]+', "", body)
    return lead, body


PL_DATE = re.compile(r"\b(\d{1,2})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(20\d{2})\b")

def find_date_iso(text: str) -> str:
    m = PL_DATE.search(text or "")
    if not m:
        return ""
    d, mo, y = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), tzinfo=timezone.utc).date().isoformat()
    except ValueError:
        return ""


_DEFAULT_NOISE = re.compile(
    r"^(\*|\d+\.)\s|^Aktualności$|^Strona główna$|^Poprzedni|^Następny|^__",
    re.IGNORECASE,
)
_DEFAULT_END_MARKERS = re.compile(
    r"^(Podziel się|"
    r"Filtruj|Wydarzenia:|Więcej$|today$|"
    r"\| pn\. \| wt\. \| śr\. \| czw\. \| pt\. \| sob\. \| nd\. \|)",
    re.IGNORECASE,
)
_CALENDAR_NOISE = re.compile(
    r"^(\d+\s+\w+\s+\d{4}|today|\|.*pn\..*wt\..*\||"
    r"\|.*culture.*party.*|\|.*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\||"
    r"^[\d\s|]+$)",
    re.IGNORECASE,
)


def _as_pattern(p, default):
    if p is None:
        return default
    if isinstance(p, str):
        return re.compile(p, re.IGNORECASE)
    return p


def clean_municipal_body(md: str, end_markers=None, noise_patterns=None) -> tuple[str, str, str]:
    """Return (title, lead, body) from a municipal article markdown."""
    md = strip_md_chrome(md)
    raw_lines = [l.strip() for l in md.splitlines() if l.strip()]
    NOISE = _as_pattern(noise_patterns, _DEFAULT_NOISE)
    END_MARKERS = _as_pattern(end_markers, _DEFAULT_END_MARKERS)
    title = ""
    body_lines = []
    cut = False
    for l in raw_lines:
        if END_MARKERS.match(l):
            cut = True
        if cut:
            break
        h = re.match(r"^#{1,4}\s+(.+)$", l)
        if h:
            t = h.group(1).strip()
            if t.lower() not in ("aktualności", "menu") and not title:
                title = t
            continue
        if NOISE.match(l) or _CALENDAR_NOISE.match(l):
            continue
        body_lines.append(l)
    body = clean("\n".join(body_lines))
    paras = [p for p in body.split("\n") if len(p) > 50]
    lead = paras[0] if paras else (body[:200] if body else "")
    lead = re.sub(r"\*{1,2}([^*]+?)\*{1,2}", r"\1", lead)
    return title, clean(lead), body
