# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-06-23

### Added — Phase 1: Foundation (SPEC Steps 1–4)
- `config.py`: Central configuration with scoring weights, thresholds, metro list, API key management, and auto-detecting mock mode.
- `db.py`: SQLite database with `businesses` and `events` tables. Upsert with dedup on `place_id`. Event logging. Enrichment-stage queries.
- `stages/discover.py`: Google Places Text Search discovery with metro caching (7-day TTL), Place Details for phone/website, and mock data generator (7 realistic med-spa profiles per metro).
- `stages/score.py`: Deficiency scoring with configurable weights. Base flags: `no_website`, `no_booking`, `not_mobile`, `no_ssl`, `dormant_social`, `no_social`. Bonus: `reviews_vs_web` (+2 when review_count ≥ 100 and web deficiency exists). Primary deficiency selection with override logic.
- `export.py`: CSV export and formatted console table for qualified leads (score ≥ threshold, status = 'new'), ranked by score desc.
- `tests/test_score.py`: 7 unit tests for scoring logic (all pass).

### Added — Phase 2: Enrichment (SPEC Steps 5–6)
- `stages/enrich_site.py`: Website analysis — platform detection (Wix, Squarespace, WordPress, Shopify, Linktree, Facebook), SSL check, mobile-friendly proxy (viewport meta). Mock mode returns realistic platform assignments.
- `stages/enrich_booking.py`: HTML-first booking platform detection scanning for Vagaro, Mindbody, Boulevard, Square, Acuity, Calendly signatures plus generic booking link/text detection.
- `tests/test_booking_detect.py`: 3 tests with HTML fixtures (Vagaro, Mindbody, no-booking). All pass.

### Added — Phase 3: Hooks & Extras (SPEC Steps 7–8)
- `stages/hooks.py`: LLM hook generation via Anthropic Messages API (claude-3-haiku). Template-based fallback for mock mode. Hooks are grounded in observed facts (review count, platform, booking absence, etc.).
- `stages/enrich_social.py`: Best-effort social signal detection — finds IG/FB links from website HTML, estimates activity status. Mock mode provides realistic dormant/active assignments.
- `stages/enrich_contact.py`: Contact enrichment with `EmailFinder` protocol (per spec §6.5). `StubEmailFinder` for real mode (returns not_attempted), `MockEmailFinder` for testing. Scrapes contact form URLs and mailto/email from HTML. Email API gated by score threshold.

### Added — Phase 4: CLI & Verification
- `pipeline.py`: Full pipeline orchestrator — discovery → site → booking → social → score → contact → hooks → export.
- `cli.py`: CLI entry point with `run` (full pipeline) and `export` (re-export) subcommands. Supports `--metros` and `--force` flags.
- `tests/test_integration.py`: 4 integration tests — full pipeline, CSV export validation, idempotent rerun dedup, scoring edge case.
- `pyproject.toml`, `.env.example`, `stages/__init__.py`, `tests/__init__.py`.

### Verified
- 14/14 tests pass (`pytest tests/ -v`).
- Full pipeline produces 4 qualified leads per metro (mock mode), ranked by deficiency score, with grounded hook text, exported to `review_queue.csv`.
- Multi-metro support verified (Austin + Dallas = 8 qualified leads).
- Idempotent rerun verified (no duplicates on `place_id`).
