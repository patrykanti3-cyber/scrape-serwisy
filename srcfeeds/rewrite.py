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
OUT = ROOT.parent / "data" / "rewritten"
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
