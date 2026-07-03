"""Deterministic date/time/location extraction for the events track.

The old flow left the raw body for the LLM to mine — but LLMs hallucinate event
dates/times. Here we pull the facts with regex FIRST and hand the model a firm
structured anchor in `EventItem.lead`, e.g.:

    "Data: 12.05.2024, Godzina: 18:00, Miejsce: Amfiteatr miejski"

Pure + offline → unit-tested without a browser or Ollama.
"""
from __future__ import annotations

import re

# Numeric date: 12.05.2024 / 12-05-2024 / 12/5/2024
_DATE_NUM = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](20\d{2})\b")

# Polish month names (genitive, as used in dates) + ascii-stripped variants.
_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5,
    "czerwca": 6, "lipca": 7, "sierpnia": 8,
    "września": 9, "wrzesnia": 9, "października": 10, "pazdziernika": 10,
    "listopada": 11, "grudnia": 12,
}
_DATE_PL = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(20\d{2})\b", re.IGNORECASE)

# Time: prefer a "godz." context, else a bare 24h HH:MM.
_TIME_CTX = re.compile(r"godz(?:\.|ina)?\s*(\d{1,2})[:.](\d{2})", re.IGNORECASE)
_TIME_BARE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

_LOC_LABEL = re.compile(
    r"(?:miejsce(?:\s+wydarzenia)?|lokalizacja|adres|gdzie)\s*[:\-\u2013]\s*(.+)",
    re.IGNORECASE)
_LOC_ADDR = re.compile(
    r"\bul\.\s*[A-ZŁŚŻŹĆĄĘÓŃ][\wąćęłńóśźż.\-]*(?:\s+\d+[A-Za-z]?)?")
_LOC_VENUE = re.compile(
    r"\b(Amfiteatr\w*|Hala [^\n,.;|]{2,40}|Dom Kultury|Ratusz\w*|Rynek|"
    r"Plac [^\n,.;|]{2,40}|Kino [^\n,.;|]{2,40}|Teatr [^\n,.;|]{2,40}|"
    r"Muzeum [^\n,.;|]{2,40}|Biblioteka\w*|Centrum [^\n,.;|]{2,40}|"
    r"Stadion\w*|Park [^\n,.;|]{2,40})")


def _find_date(text: str):
    """Return (display 'dd.mm.yyyy', iso 'yyyy-mm-dd') or ('','')."""
    m = _DATE_NUM.search(text or "")
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _DATE_PL.search(text or "")
        if not m:
            return "", ""
        d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return "", ""
    return f"{d:02d}.{mo:02d}.{y}", f"{y:04d}-{mo:02d}-{d:02d}"


def _find_time(text: str) -> str:
    m = _TIME_CTX.search(text or "")
    if not m:
        m = _TIME_BARE.search(text or "")
    if not m:
        return ""
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return ""
    return f"{h:02d}:{mi:02d}"


def _find_location(text: str) -> str:
    m = _LOC_LABEL.search(text or "")
    if m:
        loc = re.split(r"[\n|]|(?:\.\s)|(?:  )", m.group(1))[0].strip(" .,-\u2013\u2014*_>\"'")
        if 2 <= len(loc) <= 100:
            return loc
    m = _LOC_ADDR.search(text or "")
    if m:
        return m.group(0).strip()
    m = _LOC_VENUE.search(text or "")
    if m:
        return m.group(0).strip()[:100]
    return ""


def extract_event_facts(text: str) -> dict:
    """Return {date, date_iso, time, location, lead}. `lead` is the structured
    anchor string (empty if nothing was found)."""
    date, date_iso = _find_date(text)
    time = _find_time(text)
    location = _find_location(text)
    parts = []
    if date:
        parts.append(f"Data: {date}")
    if time:
        parts.append(f"Godzina: {time}")
    if location:
        parts.append(f"Miejsce: {location}")
    return {
        "date": date,
        "date_iso": date_iso,
        "time": time,
        "location": location,
        "lead": ", ".join(parts),
    }
