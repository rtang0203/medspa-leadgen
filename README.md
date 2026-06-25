# Med-Spa Lead Discovery & Qualification Pipeline

A batch pipeline that finds med-spas and day-spas across many metros, inspects each one's web presence, scores how badly it needs marketing/web services, and produces a **ranked review queue** of the best prospects — each with a contact method and a specific, evidence-grounded opening line.

A human reviews, edits, and sends. **The system never sends anything itself.**

---

## Quick Start (Mock Mode)

No API keys needed — mock mode generates realistic sample data so you can see the full pipeline in action:

```bash
# Install dependencies
pip3 install -r requirements.txt

# Run the pipeline
python3 cli.py run --metros "Austin, TX"

# Output: ranked console table + review_queue.csv
```

## Setup for Real Data

### 1. Get API Keys

| Key | Purpose | Required? |
|-----|---------|-----------|
| `GOOGLE_PLACES_API_KEY` | Discover businesses via Google Places Text Search + Details APIs | Yes, for real discovery |
| `ANTHROPIC_API_KEY` | Generate personalized hook text via Claude | Optional (template fallback works well) |

**Google Places API setup:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use an existing one)
3. Enable the **Places API** (not "Places API (New)" — use the legacy one for Text Search)
4. Create an API key under **APIs & Services → Credentials**
5. Restrict the key to the Places API for safety

**Anthropic API setup:**
1. Go to [console.anthropic.com](https://console.anthropic.com/)
2. Create an API key
3. The pipeline uses `claude-haiku-4-5` (cheapest/fastest model) — cost is negligible

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```
GOOGLE_PLACES_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
MOCK_MODE=false
```

The pipeline auto-detects mock mode when API keys are missing. Set `MOCK_MODE=false` explicitly once keys are configured.

### 3. Run

```bash
# Single metro
python3 cli.py run --metros "Austin, TX"

# Multiple metros (semicolon-separated)
python3 cli.py run --metros "Austin, TX;Dallas, TX;Houston, TX"

# Use default metros from config.py
python3 cli.py run

# Force re-discovery (ignores 7-day cache)
python3 cli.py run --metros "Austin, TX" --force

# Re-export from existing database without re-running the pipeline
python3 cli.py export
```

---

## How It Works

The pipeline runs 8 stages sequentially. Every stage is **idempotent** — reruns skip already-completed work and fill in gaps.

```
discover → enrich_site → enrich_booking → enrich_social → score → enrich_contact → hooks → export
```

| Stage | What it does | Data source |
|-------|-------------|-------------|
| **Discovery** | Finds med-spas in a metro | Google Places Text Search API |
| **Site Enrichment** | Fetches homepage, detects platform (Wix/Squarespace/WordPress/etc), checks SSL and mobile-friendliness | HTTP fetch + HTML analysis |
| **Booking Enrichment** | Scans HTML for booking widget signatures (Vagaro, Mindbody, Boulevard, Square, Acuity, Calendly, GlossGenius, Zenoti, Booksy, Fresha, Jane, Mangomint, and others) plus generic "book"/"schedule" link detection | HTML fingerprinting |
| **Social Enrichment** | Finds Instagram/Facebook links on the website, estimates activity status | HTML link scanning |
| **Scoring** | Calculates a deficiency score based on missing/weak web presence signals | Pure logic over DB fields |
| **Contact Enrichment** | Finds contact forms, scrapes emails from HTML, calls EmailFinder API for high-scoring leads | HTML scraping + pluggable API |
| **Hook Generation** | Writes a specific, fact-grounded opening line referencing the business's primary deficiency | Anthropic Claude API (or templates) |
| **Export** | Outputs a ranked CSV and console table of qualified leads | SQLite query |

### Scoring Logic

Each deficiency adds to the score. Higher score = worse web presence = better prospect.

| Deficiency | Weight | Trigger |
|-----------|--------|---------|
| `no_website` | 3 | No site, or only Linktree/Facebook |
| `no_booking` | 2 | No online booking widget detected |
| `not_mobile` | 2 | No viewport meta tag (mobile-unfriendly) |
| `no_ssl` | 1 | HTTPS fails or site is HTTP-only |
| `dormant_social` | 1 | Social accounts exist but inactive |
| `no_social` | 1 | No social links found |
| `reviews_vs_web` (bonus) | +2 | 100+ reviews AND any web deficiency — "clearly busy, clearly leaking" |

Leads with `deficiency_score >= 4` (configurable in `medspa_leads/config.py`) are qualified for contact enrichment via email-finder APIs.

---

## Project Structure

```
medspa-leadgen/
├── cli.py                      # CLI entry point
├── medspa_leads/               # main package
│   ├── __init__.py
│   ├── config.py               # Metros, scoring weights, thresholds, API keys
│   ├── db.py                   # SQLite schema, upsert/query helpers, event logging
│   ├── pipeline.py             # Orchestrates all 8 stages in order
│   ├── export.py               # CSV + console table export
│   └── stages/
│       ├── __init__.py
│       ├── discover.py         # Google Places API → upsert businesses
│       ├── enrich_site.py      # Website fetch + platform/mobile/SSL analysis
│       ├── enrich_booking.py   # Booking widget fingerprint detection
│       ├── enrich_social.py    # Social link discovery + activity estimation
│       ├── enrich_contact.py   # Contact form + email scraping + EmailFinder API
│       ├── score.py            # Deficiency scoring + primary deficiency selection
│       └── hooks.py            # LLM hook generation (Anthropic or templates)
├── tests/
│   ├── test_score.py           # 7 unit tests for scoring logic
│   ├── test_booking_detect.py  # 9 tests with HTML fixtures
│   ├── test_integration.py     # 4 E2E pipeline tests (mock mode)
│   └── fixtures/               # Sample HTML pages for offline testing
├── pyproject.toml
├── .env.example
├── .gitignore
└── SPEC.md                     # Original build specification
```

---

## Output

### Console Table

```
--- Ranked Review Queue (4 Leads) ---
-------------------------------------------------------------------------------------
| Name                   | Metro        | Score | Primary Def.         | Phone          |
-------------------------------------------------------------------------------------
| Nirvana Wellness       | Austin, TX   | 9     | no_website           | N/A            |
| Classic Derm & Spa     | Austin, TX   | 8     | reviews_vs_web       | +1-512-555-... |
| Zen Day & Medical Spa  | Austin, TX   | 6     | no_website           | +1-512-555-... |
| Radiant Glow Med Spa   | Austin, TX   | 5     | no_website           | +1-512-555-... |
-------------------------------------------------------------------------------------
```

### CSV (`review_queue.csv`)

Contains: name, metro, score, primary deficiency, phone, email, email status, website URL, booking platform, social status, hook text, Google Maps link.

### Hook Examples

> "noticed classic derm & has 312+ five-star reviews but no online booking on the site — you're probably losing after-hours bookings to whoever picks up the phone first."

> "noticed radiant glow has a strong social presence but relies on Facebook for details — a dedicated website would help you rank much higher in local Google searches."

---

## Running Tests

```bash
pip3 install pytest
python3 -m pytest tests/ -v
```

All 20 tests run in ~0.2 seconds using mock data (no API calls).

---

## Cost & Rate Limits

With real API keys:

- **Google Places:** ~3 Text Search calls per metro (one per query variant) + 1 Details call per new business. Expect ~$5–15 per metro depending on result count. The pipeline caches metro discovery for 7 days to avoid redundant calls.
- **Anthropic:** One `claude-haiku-4-5` call per lead. Cost is under $0.01 per metro.
- **Email finder:** Stubbed out for PoC. Wire a provider (Hunter, Apollo, Dropcontact) into `medspa_leads/stages/enrich_contact.py` by implementing the `EmailFinder` protocol.

---

## Extending

### Add an email finder provider

Implement the `EmailFinder` protocol in `medspa_leads/stages/enrich_contact.py`:

```python
class HunterEmailFinder:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def find(self, *, domain: str, business_name: str, owner_name: str | None) -> dict:
        # Call Hunter.io API
        # Return {"email": "...", "status": "verified" | "guessed" | "not_found"}
        ...
```

Then update `get_email_finder()` to return your implementation when the API key is present.

### Tune scoring weights

Edit `medspa_leads/config.py`:

```python
WEIGHTS = {
    "no_website": 3,
    "no_booking": 2,
    ...
}
GOOD_LEAD_THRESHOLD = 4
```

Run a metro, eyeball the top 20, adjust, repeat. The weights are the product.

### Add new metros

Edit `DEFAULT_METROS` in `medspa_leads/config.py`, or pass them via CLI:

```bash
python3 cli.py run --metros "Miami, FL;Tampa, FL;Orlando, FL"
```
