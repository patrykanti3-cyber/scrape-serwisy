import sys
import pathlib

SRCFEEDS = pathlib.Path(__file__).resolve().parent.parent / "srcfeeds"
sys.path.insert(0, str(SRCFEEDS))

from rewrite import resolve_category


def test_police_source_forced_na_sygnale():
    assert resolve_category("police", "sport", "kultura") == "na-sygnale"


def test_section_hint_na_sygnale_honored():
    # municipal source, but the section declared na-sygnale -> honored (Krok 3)
    assert resolve_category("municipal", "na-sygnale", "") == "na-sygnale"


def test_section_hint_lifestyle_honored():
    assert resolve_category("municipal", "lifestyle", "wiadomosci") == "lifestyle"


def test_section_hint_topic_categories():
    for c in ("sport", "kultura", "biznes", "ogloszenia", "wiadomosci"):
        assert resolve_category("municipal", c, "") == c


def test_no_hint_uses_validated_llm_category():
    assert resolve_category("municipal", "", "kultura") == "kultura"


def test_no_hint_invalid_llm_defaults_wiadomosci():
    assert resolve_category("municipal", "", "totalny-smiec") == "wiadomosci"
    assert resolve_category("municipal", "", "") == "wiadomosci"


def test_przydatne_is_not_an_article_category():
    # przydatne is filtered out before rewrite; if it ever reaches here it is NOT
    # honored as an article category -> falls through to the LLM/default.
    assert resolve_category("municipal", "przydatne", "kultura") == "kultura"
    assert resolve_category("municipal", "przydatne", "") == "wiadomosci"
