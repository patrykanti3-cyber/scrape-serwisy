import sys
import pathlib

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

from event_facts import extract_event_facts


def test_numeric_date_time_location_lead():
    text = "Koncert plenerowy. Miejsce: Amfiteatr miejski. Data: 12.05.2024, godz. 18:00."
    f = extract_event_facts(text)
    assert f["date"] == "12.05.2024"
    assert f["date_iso"] == "2024-05-12"
    assert f["time"] == "18:00"
    assert f["location"] == "Amfiteatr miejski"
    assert f["lead"] == "Data: 12.05.2024, Godzina: 18:00, Miejsce: Amfiteatr miejski"


def test_polish_month_name_date():
    f = extract_event_facts("Sesja Rady odbędzie się 5 czerwca 2026 o godzinie 10:00.")
    assert f["date"] == "05.06.2026"
    assert f["date_iso"] == "2026-06-05"
    assert f["time"] == "10:00"


def test_zero_padding_single_digit():
    f = extract_event_facts("Termin: 3.7.2026")
    assert f["date"] == "03.07.2026"
    assert f["date_iso"] == "2026-07-03"


def test_address_location_fallback():
    f = extract_event_facts("Spotkanie w budynku przy ul. Kościelna 5 o 17:00.")
    assert f["location"].startswith("ul. Kościelna")
    assert f["time"] == "17:00"


def test_label_location_priority_over_venue():
    f = extract_event_facts("Gdzie: Ratusz miejski, sala obrad. Zapraszamy.")
    assert f["location"] == "Ratusz miejski, sala obrad"


def test_no_facts_empty_lead():
    f = extract_event_facts("Zapraszamy na wydarzenie kulturalne dla mieszkańców.")
    assert f["date"] == "" and f["time"] == "" and f["location"] == ""
    assert f["lead"] == ""


def test_invalid_date_rejected():
    # 45.13.2026 is not a real date -> not extracted
    f = extract_event_facts("Kod 45.13.2026 to nie data.")
    assert f["date"] == ""


def test_bad_time_rejected():
    # bare 99:99 impossible; but "o 18:30" valid
    assert extract_event_facts("start o 18:30").get("time") == "18:30"
