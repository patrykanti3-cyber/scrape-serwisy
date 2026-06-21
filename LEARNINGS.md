# LEARNINGS

Notes on the `srcfeeds/` scraping + cleaning pipeline.

## Architecture

- `srcfeeds/scraper.py` — orchestration: RSS discovery, crawl4ai fetch, `NewsItem` model, JSONL output to `data/raw/<city>.jsonl`.
- `srcfeeds/cleaners.py` — pure text functions (no I/O, no network). Extracted from the scraper so they are unit-testable in isolation.
- `tests/test_cleaners.py` — exercises the cleaners against synthetic markdown plus one real sample (`slupsk_art_0.md`).

The split exists because the cleaning logic is the brittle part (markdown chrome varies per source), and it must be testable without spinning up a headless browser.

## Cleaning logic

### `strip_md_chrome`
- Images are saved to a placeholder (`\x00IMG<n>\x00`) **before** link stripping, then restored at the end. This is required because the image syntax `![alt](url)` overlaps the link syntax `[text](url)` — without the placeholder dance the link regex would mangle images.
- Order matters: `javascript:`/`#` anchor links collapse to their label first, then generic links, then leftover artifacts (`")`, `()`).
- Polish portal chrome (`Drukuj`, `Powrót`, `Udostępnij`, `Link skopiowany…`) is dropped by name.

### `clean_police_body`
- Police pages put the lead in the first `**bold**` span. We extract that as the lead, then strip all `*` for the body.
- `re.DOTALL` on the bold match so multi-line leads are captured.

### `clean_municipal_body`
- Returns `(title, lead, body)`.
- Title comes from the first `#`–`####` heading that is not `aktualności`/`menu`.
- `end_markers` and `noise_patterns` are injectable (string or compiled regex via `_as_pattern`) so per-source quirks can be tuned from `sources.json` without editing the module. Defaults cover calendar widgets, "Podziel się", pagination, etc.
- Lead = first paragraph longer than 50 chars (filters out stray short lines), with bold markers stripped.

### `find_date_iso`
- Only parses **numeric** dates (`02.06.2026` → `2026-06-02`). Polish month names (`15 czerwca 2026`) intentionally return `""` — month-name parsing was out of scope and ambiguous enough to skip.

## Testing

- Run: `python3 -m pytest tests/ -q` (use `python3`; `python` is not on PATH on this machine — macOS / Python 3.14).
- Status: **8 passed, 0 failures**.
- `tests/` injects `srcfeeds/` onto `sys.path`, so cleaners import as a top-level module (`from cleaners import ...`) in both the tests and `scraper.py`.
