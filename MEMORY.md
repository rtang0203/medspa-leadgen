# Memory

## 2026-06-23: Initial Project Decisions

### 1. Mock/Offline Mode Support
- **Decision:** Implement a toggleable mock/offline mode for both Google Places discovery and Anthropic hook generation.
- **Why:** Local test environment does not have active `GOOGLE_PLACES_API_KEY` or `ANTHROPIC_API_KEY`. Mocking allows end-to-end execution, integration tests, and easy validation.
- **Rejected:** Strictly requiring active API keys, because it would block pipeline verification and local testing.

### 2. Architecture & File Structure
- **Decision (initial):** Place all files in the root as outlined in `SPEC.md` (§9). Stages under `stages/`.
- **Decision (refactored):** Move library code into a `medspa_leads/` Python package. Only `cli.py` and repo metadata remain at root. Stages live at `medspa_leads/stages/`. All internal imports use relative (`from . import`, `from .. import`).
- **Why refactored:** Flat root mixed library modules (`db.py`, `config.py`, `pipeline.py`, `export.py`) with repo files (`SPEC.md`, `CHANGELOG.md`, `cli.py`). No package boundary. The package structure is standard Python, makes imports explicit, and separates concerns.
- **Rejected:** Leaving flat structure — functional but messy; also rejected a `src/` layout as over-engineering for this project size.

### 3. Dependencies
- **Decision:** Use `requests` for Google Places API and HTTP fetching, `beautifulsoup4` for HTML parsing, and `python-dotenv` for env configuration. Use Python's standard `sqlite3` library.
- **Why:** Avoids heavy third-party SDK dependencies. Keeps things simple and flexible.

### 4. Scoring Logic
- **Decision:** `reviews_vs_web` bonus overrides `primary_deficiency` when applicable (score >= 100 reviews AND web deficiency). This matches the spec's "strongest hook archetype" guidance.
- **Rejected:** Having `reviews_vs_web` just add to score without changing primary — the spec explicitly says "The bonus can override to reviews_vs_web when present."

### 5. Hook Generation
- **Decision:** Template-based hooks in mock mode, Anthropic API in real mode with template fallback on API failure.
- **Why:** Template hooks are grounded in observed data and produce high-quality output without API cost. LLM adds variety and natural language polish when available.

### 6. Email Finder Interface
- **Decision:** Protocol-based `EmailFinder` with `StubEmailFinder` (real mode, returns not_attempted) and `MockEmailFinder` (test mode, returns simulated results). Gated by score threshold.
- **Why:** Follows spec §6.5 exactly — the seam is defined, provider wiring is deferred. Credits spent only on high-scoring prospects.

### 7. Export All Businesses (2026-06-25)
- **Decision:** Remove score threshold from export/table/CSV — show ALL discovered businesses ranked by score, not just those ≥ GOOD_LEAD_THRESHOLD.
- **Why:** User wants full visibility into all found businesses regardless of score.
- **Rejected:** Keeping the threshold filter — user explicitly requested all results.

### 8. Generate Hooks for All Businesses (2026-06-25)
- **Decision:** Remove score gate from hook generation — generate hooks for all businesses, not just high-scorers.
- **Why:** Since all businesses are now exported, they all need hooks. GOOD_LEAD_THRESHOLD still exists in config for potential future use.
---

## Session Summary (2026-06-23)

### Worked on
- Full implementation of SPEC.md pipeline (all 8 build order steps)

### Completed
- Steps 1–4 (Foundation): db.py, discover.py, score.py, export.py — all working and producing ranked lead output
- Steps 5–6 (Enrichment): enrich_site.py, enrich_booking.py — platform/SSL/mobile detection and booking fingerprinting
- Steps 7–8 (Hooks & Extras): hooks.py, enrich_social.py, enrich_contact.py — LLM hooks, social signals, contact/email enrichment
- CLI (cli.py) and pipeline orchestrator (pipeline.py)
- 14 tests: 7 scoring, 3 booking detection, 4 integration — all passing

### Decisions made
- Mock mode auto-detects when API keys are missing
- Template hooks serve as both fallback and primary mock-mode output
- Email finder uses Protocol pattern for future provider swapping

### Next session priorities
- Wire real Google Places API key and test with live data
- Wire Anthropic API key for LLM-generated hooks
- Tune scoring weights against real metro output
- Consider Playwright for JS-rendered booking widget detection (measure false-negative rate first)
- Wire a real email finder provider (Hunter/Apollo) behind the EmailFinder interface

## 2026-06-25: dotenv Override Bug

### 9. load_dotenv override=True — REVERTED
- **Original decision:** Changed `load_dotenv()` to `load_dotenv(override=True)` in config.py.
- **Reverted (2026-06-28):** User intentionally removed `override=True`. The default `load_dotenv()` behavior (don't override existing env vars) is the desired behavior.
- **Background:** VS Code's `python.terminal.useEnvFile` was the original symptom, but that was resolved separately. Keeping default dotenv behavior avoids unexpected env var overrides in production.

### 10. Anthropic API — credits and model update
- **Note:** Anthropic API key is valid but account initially had zero credits (400 error). After adding credits, discovered `claude-3-haiku-20240307` was retired (404). Updated to `claude-haiku-4-5-20251001`.

### 11. Booking detection broadened (2026-06-25)
- **Decision:** Added 9 new booking platform signatures (GlossGenius, Zenoti, Booksy, Fresha, Jane, Mangomint, Aesthetic Record, Pabau, Setmore). Broadened link detection to match any `<a>` with "book"/"schedule" in text or href. Added `<button>`/`<input>` scanning. Removed body-text matching to avoid false positives on prose.
- **Why:** Real-world sites like chicagomedspa.com (GlossGenius) and viomedspa.com (Zenoti) were incorrectly flagged as `no_booking`.
- **Rejected:** Headless browser approach — too slow for 40+ sites per metro. The HTML-first approach catches most cases now.

### 12. Use requirements.txt, not pyproject.toml (2026-06-25)
- **Decision:** Added `requirements.txt` as the primary dependency file. `pyproject.toml` still exists but `requirements.txt` is what README references.
- **Why:** Simpler, more widely recognized. Not publishing to PyPI.

---

## Session Summary (2026-06-25)

### Worked on
- First real-data run with live Google Places and Anthropic API keys
- Debugging .env loading, API connectivity, and booking detection accuracy

### Completed
- Fixed `load_dotenv(override=True)` — .env values now override shell env vars
- Updated Anthropic model from retired `claude-3-haiku-20240307` to `claude-haiku-4-5-20251001`
- Removed score threshold gating from export and hook generation — all businesses now included
- Broadened booking detection: 9 new platforms, generic link/button matching, false-positive guard
- Added 6 new booking detection tests (9 total), updated integration test
- Added `requirements.txt`, updated README for accuracy
- 20/20 tests passing

### Decisions made
- `load_dotenv(override=True)` is the default going forward
- `requirements.txt` over `pyproject.toml` for deps (recorded in global CLAUDE.md)
- MEMORY.md and ERRORS.md gitignored; SPEC.md, CHANGELOG.md, README.md tracked

### Next session priorities
- Test with additional metros and get feedback
- Tune scoring weights against real data
- Consider Playwright for JS-rendered booking widget detection (measure false-negative rate first)
- Wire a real email finder provider (Hunter/Apollo) behind the EmailFinder interface

## 2026-07-10: Dashboard competitor scraper

- Added `dashboard-scrape` as a Supabase-only batch command that preserves the SQLite lead-generation flow; competitors and scrape snapshots are global by Google Place ID, while `tenant_competitors` holds each tenant's monitoring choice.
- `dashboard-scrape` now batches every distinct primary-location metro once; `competitor_markets` retains global market provenance, completed-market cache markers prevent redundant Places calls, and tenant links synchronize afterward without overriding `tracked=false`.
