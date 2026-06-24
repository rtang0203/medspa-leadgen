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
