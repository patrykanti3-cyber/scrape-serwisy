import sys
import pathlib

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

from rewrite import build_event_prompt


def test_lead_embedded_as_hard_anchor():
    r = {
        "lead": "Data: 12.05.2026, Godzina: 18:00, Miejsce: Amfiteatr miejski",
        "title": "Koncert plenerowy",
        "body": "Opis wydarzenia dla mieszkańców.",
    }
    p = build_event_prompt(r)
    # the deterministic anchor must appear verbatim
    assert "Data: 12.05.2026, Godzina: 18:00, Miejsce: Amfiteatr miejski" in p
    assert "USTALONE FAKTY" in p
    # explicit no-hallucination instruction present
    assert "NIE zmieniaj" in p and "NIE wymyślaj" in p
    # source material still included
    assert "Koncert plenerowy" in p and "Opis wydarzenia" in p


def test_fallback_when_no_facts():
    p = build_event_prompt({"lead": "", "title": "X", "body": "y"})
    assert "brak ustrukturyzowanych faktów" in p
    assert "NIE podawaj konkretnej daty" in p
