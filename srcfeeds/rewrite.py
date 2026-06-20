"""rewrite.py — stage 2: feed scraped source items into an LLM (Ollama) to produce
a fresh, re-edited Polish article, keeping facts but rephrasing entirely, and
appending source attribution (the same footprint the portals use).

Usage:
  python rewrite.py <city> [limit] [--model NAME]
Reads : data/raw/<city>.jsonl
Writes: data/rewritten/<city>.jsonl
"""
from __future__ import annotations
import json, sys, urllib.request
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

Zwróć WYŁĄCZNIE obiekt JSON o polach:
  "title" – rzeczowy tytuł (max 120 znaków),
  "lead"  – 1–2 zdania wprowadzenia,
  "body"  – treść artykułu, akapity oddzielone podwójnym znakiem nowej linii.

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


def generate(model: str, prompt: str, timeout: int = 300) -> str:
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    city = args[0] if args else "slupsk"
    limit = int(args[1]) if len(args) > 1 else 1000
    model = None
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
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
            prompt = PROMPT.format(source=r["source_credit"], title=r["title"],
                                   body=(r["body"] or r["lead"])[:6000])
            print(f"[{i}/{len(rows)}] {r['source_type']:9} {r['title'][:60]!r} ...", flush=True)
            try:
                resp = generate(model, prompt)
                art = json.loads(resp)
            except Exception as e:
                print("   ! rewrite failed:", repr(e)[:200])
                continue
            attribution = (f"na podstawie: {r['source_credit']}.\n"
                           f"Ilustracja wykorzystana w artykule została pobrana z zewnętrznego "
                           f"źródła ({r['source_credit']}). W przypadku zastrzeżeń dotyczących "
                           f"praw do zdjęcia prosimy o kontakt.")
            rec = {
                "id": r["id"], "city": r["city"],
                "title": art.get("title", "").strip(),
                "lead": art.get("lead", "").strip(),
                "body": art.get("body", "").strip(),
                "image_url": r["image_url"],
                "published": r["published"],
                "source_type": r["source_type"],
                "source_name": r["source_name"],
                "source_url": r["source_url"],
                "attribution": attribution,
                "model": model,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n_ok += 1
    print(f"==> {n_ok}/{len(rows)} rewritten -> {out_path}")


if __name__ == "__main__":
    main()
