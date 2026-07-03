"""rewrite.py — stage 2: feed scraped source items into an LLM (Ollama) to produce
a fresh, re-edited Polish article, keeping facts but rephrasing entirely, and
appending source attribution (the same footprint the portals use).

Usage:
  python rewrite.py <city> [limit] [--model NAME]
Reads : data/raw/<city>.jsonl
Writes: data/rewritten/<city>.jsonl
"""
from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
RAW = ROOT.parent / "data" / "raw"
EVENTS_RAW = ROOT.parent / "data" / "raw" / "events"
OUT = ROOT.parent / "data" / "rewritten"
EVENTS_OUT = ROOT.parent / "data" / "rewritten" / "events"
OUT.mkdir(parents=True, exist_ok=True)
OLLAMA = "http://localhost:11434"

PROMPT = """Jesteś redaktorem lokalnego portalu informacyjnego. Na podstawie poniższego materiału źródłowego napisz NOWY, samodzielny artykuł po polsku.

Zasady:
- Zachowaj WSZYSTKIE fakty: daty, liczby, miejsca, nazwy, kwoty.
- Całkowicie przeredaguj treść własnymi słowami — NIE kopiuj zdań ze źródła.
- Ton: neutralny, dziennikarski, zwięzły.
- Nie dodawaj informacji, których nie ma w źródle. Nie zmyślaj.
- Nie pisz o sobie ani o procesie redakcji.

Struktura treści (pole "body", Markdown):
- Akapity oddzielaj podwójnym znakiem nowej linii.
- Jeśli artykuł ma 4+ akapitów, podziel go na 2-3 sekcje tematyczne, każda
  z nagłówkiem w formacie "## Krótki nagłówek sekcji". Krótkie artykuły zostaw
  bez nagłówków.
- Jeśli w MATERIALE ŹRÓDŁOWYM jest dosłowna wypowiedź (cytat osoby, organizatora,
  urzędnika), zacytuj ją jako "> treść cytatu". Cytuj WYŁĄCZNIE słowa obecne
  w źródle — NIE twórz cytatów, których tam nie ma.

Zwróć WYŁĄCZNIE obiekt JSON o polach:
  "title"    – rzeczowy tytuł (max 120 znaków),
  "lead"     – 1–2 zdania wprowadzenia,
  "body"     – treść artykułu wg struktury powyżej,
  "category" – jedna z: "wiadomosci", "sport", "kultura", "biznes", "ogloszenia".
               Dobierz wg tematu: sport (zawody, kluby, rozgrywki), kultura
               (wydarzenia kulturalne, sztuka, biblioteka, muzeum), biznes
               (gospodarka, firmy, inwestycje, fundusze), ogloszenia (nabory,
               konsultacje, komunikaty urzędowe, oferty). Gdy nie pasuje
               jednoznacznie — "wiadomosci".

MATERIAŁ ŹRÓDŁOWY (źródło: {source}):
Tytuł: {title}
Treść:
{body}
"""


EVENT_PROMPT = """Jesteś redaktorem lokalnego portalu. Na podstawie materiału o wydarzeniu napisz krótką, rzeczową zapowiedź po polsku.

USTALONE FAKTY WYDARZENIA (źródło deterministyczne, wyodrębnione regexem — traktuj jako PRAWDĘ; NIE zmieniaj i NIE wymyślaj innej daty, godziny ani miejsca):
{anchor}

Zasady:
- Datę, godzinę i miejsce bierz WYŁĄCZNIE z sekcji USTALONE FAKTY powyżej.
  Jeśli któregoś z tych faktów tam nie ma — NIE zgaduj, po prostu go pomiń.
- Nie dodawaj informacji spoza materiału źródłowego. Nie zmyślaj.
- Ton: zwięzły, zapraszający, dziennikarski.

Zwróć WYŁĄCZNIE obiekt JSON o polach:
  "title"   – tytuł wydarzenia (max 120 znaków),
  "summary" – 1–2 zdania zapowiedzi; jeśli znane, zawrzyj datę/godzinę/miejsce
              DOKŁADNIE jak w USTALONE FAKTY,
  "body"    – opis wydarzenia (Markdown).

MATERIAŁ ŹRÓDŁOWY:
Tytuł: {title}
Treść:
{body}
"""


def build_event_prompt(r: dict) -> str:
    """Compose the event-rewrite prompt, injecting the deterministic
    date/time/location anchor (EventItem.lead) so the LLM cannot hallucinate the
    schedule. Falls back to an explicit 'do not guess' instruction when the
    scraper found no structured facts."""
    anchor = (r.get("lead") or "").strip() or (
        "(brak ustrukturyzowanych faktów — NIE podawaj konkretnej daty, godziny "
        "ani miejsca, jeśli nie wynikają wprost z treści źródła)")
    return EVENT_PROMPT.format(
        anchor=anchor,
        title=r.get("title", ""),
        body=(r.get("body") or "")[:6000],
    )


def list_models() -> list[str]:
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=10) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception as e:
        print("! cannot reach Ollama:", e); return []


def generate(model: str, prompt: str, timeout: int = int(os.environ.get("OLLAMA_TIMEOUT", "600"))) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.7, "num_ctx": 8192},
    }
    req = urllib.request.Request(
        OLLAMA + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("response", "")


def rewrite_events(city: str, model: str, limit: int) -> None:
    """Rewrite scraped events (data/raw/events/<city>.jsonl) with the LLM, using
    EventItem.lead as a hard, non-negotiable date/time/location anchor. Output:
    data/rewritten/events/<city>.jsonl (keeps `lead` + the reliable `published`)."""
    src_path = EVENTS_RAW / f"{city}.jsonl"
    if not src_path.exists():
        print(f"No raw events: {src_path}"); return
    rows = [json.loads(l) for l in open(src_path, encoding="utf-8") if l.strip()][:limit]
    EVENTS_OUT.mkdir(parents=True, exist_ok=True)
    out_path = EVENTS_OUT / f"{city}.jsonl"
    n_ok = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            prompt = build_event_prompt(r)
            print(f"[{i}/{len(rows)}] EVENT {r.get('title','')[:50]!r} | anchor={r.get('lead','')!r}", flush=True)
            try:
                art = json.loads(generate(model, prompt))
            except Exception as e:
                print("   ! event rewrite failed:", repr(e)[:200]); continue
            credit = r.get("source_credit") or r.get("source_name") or "źródło"
            rec = {
                "id": r.get("id", ""), "city": r.get("city", ""),
                "title": art.get("title", "").strip(),
                "summary": art.get("summary", "").strip(),
                "body": art.get("body", "").strip(),
                "lead": r.get("lead", ""),           # deterministic anchor preserved
                "image_url": r.get("image_url", ""),
                "published": r.get("published", ""),  # reliable date from extractor
                "source_name": r.get("source_name", ""),
                "source_url": r.get("source_url", ""),
                "attribution": f"na podstawie: {credit}.",
                "model": model,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n_ok += 1
    print(f"==> {n_ok}/{len(rows)} events rewritten -> {out_path}")


def main():
    argv = sys.argv[1:]
    model = None
    if "--model" in argv:
        i = argv.index("--model")
        model = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]              # drop the flag AND its value
    args = [a for a in argv if not a.startswith("--")]
    city = args[0] if args else "slupsk"
    limit = int(args[1]) if len(args) > 1 else 1000
    models = list_models()
    if not models:
        print("No Ollama models / server. Aborting."); return
    model = model or ("kimi-k2.6:cloud" if "kimi-k2.6:cloud" in models else models[0])
    print(f"Using model: {model}")

    if "--events" in sys.argv:
        rewrite_events(city, model, limit)
        return

    src_path = RAW / f"{city}.jsonl"
    if not src_path.exists():
        print(f"No raw data: {src_path}"); return
    rows = [json.loads(l) for l in open(src_path, encoding="utf-8") if l.strip()][:limit]

    out_path = OUT / f"{city}.jsonl"
    n_ok = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            prompt = PROMPT.format(source=r.get("source_credit", ""), title=r.get("title", ""),
                                   body=(r.get("body") or r.get("lead") or "")[:6000])
            print(f"[{i}/{len(rows)}] {r.get('source_type', '?'):9} {r.get('title', '')[:60]!r} ...", flush=True)
            try:
                resp = generate(model, prompt)
                art = json.loads(resp)
            except Exception as e:
                print("   ! rewrite failed:", repr(e)[:200])
                continue
            credit = r.get("source_credit") or r.get("source_name") or "źródło"
            attribution = (f"na podstawie: {credit}.\n"
                           f"Ilustracja wykorzystana w artykule została pobrana z zewnętrznego "
                           f"źródła ({credit}). W przypadku zastrzeżeń dotyczących "
                           f"praw do zdjęcia prosimy o kontakt.")
            # Category precedence: police is fixed (na-sygnale); a deterministic
            # section hint from the scraper wins next; otherwise trust the LLM's
            # topic classification, validated against the allowed enum.
            stype = r.get("source_type", "municipal")
            ALLOWED = ("wiadomosci", "sport", "kultura", "biznes", "ogloszenia")
            hint = (r.get("category_hint") or "").strip().lower()
            if stype == "police":
                category = "na-sygnale"
            elif hint in ALLOWED:
                category = hint
            else:
                c = (art.get("category") or "").strip()
                category = c if c in ALLOWED else "wiadomosci"
            rec = {
                "id": r.get("id", ""), "city": r.get("city", ""),
                "title": art.get("title", "").strip(),
                "lead": art.get("lead", "").strip(),
                "body": art.get("body", "").strip(),
                "category": category,
                "image_url": r.get("image_url", ""),
                "published": r.get("published", ""),
                "source_type": stype,
                "source_name": r.get("source_name", ""),
                "source_url": r.get("source_url", ""),
                "attribution": attribution,
                "model": model,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n_ok += 1
    print(f"==> {n_ok}/{len(rows)} rewritten -> {out_path}")


if __name__ == "__main__":
    main()
