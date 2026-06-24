# Med-Spa Lead Discovery & Qualification Pipeline — Build Spec

**Status:** proof of concept
**Audience:** (1) the business owner, to confirm assumptions; (2) a Claude Code build agent, to implement.

---

## 0. What this is, in one paragraph

A batch pipeline that finds med-spas / day-spas across many metros, inspects each one's web presence (website, online-booking, reviews, social), scores how badly it needs the services we sell, and produces a **ranked review queue** of the best prospects — each with a contact method and a specific, evidence-grounded opening line. A human reviews, edits, and sends. **The system never sends anything itself.**

This is a *qualification* engine, not a spam cannon. The value is the deficiency score and the grounded hook — not volume.

---

## 1. Assumptions to confirm with the owner (DO THIS BEFORE BUILDING)

These drive the scoring logic. If they're wrong, the output is wrong.

1. **Offer = websites + marketing for med-spas.** Confirm the exact deliverables.
2. **Target deficiencies** (the problems the offer fixes), in rough priority:
   - No real website (Linktree / Facebook-page-only / nothing)
   - Bad website (not mobile-friendly, no SSL, obviously outdated/template)
   - No online booking
   - Weak / dormant social
   - High reviews + poor web presence (strong signal: happy customers, leaky funnel)
3. **Buyer = independent owner-operators** (not large franchises/chains). Affects who we try to reach and how.
4. **Primary outreach channel = phone / contact form to start**, email as a bonus when cheaply obtainable.
5. **Geography = many metros**, run repeatedly over time.

> If the owner says the real differentiator is something not in the deficiency list above (e.g. "we mainly do paid-ads management"), the scoring weights in §5 must change accordingly.

---

## 2. Scope (PoC boundaries)

**In scope:**
- Discovery via Google Places API
- Website fetch + analysis (platform, mobile-friendly, SSL)
- Booking-platform detection
- Review signal (from Places)
- Social presence signal (best-effort, from links found on site/Places)
- Deficiency scoring + primary-deficiency selection
- LLM hook generation, grounded in collected facts
- Persistent store (SQLite) with dedup + status tracking
- Ranked review-queue output (CSV + a simple readable table)

**Explicitly OUT of scope for PoC:**
- Any automated sending
- Job queue / orchestrator / continuous daemon (run as a batch command)
- Account warmup / health monitoring (we send manually from the owner's own identity — no accounts to manage)
- Building our own email-finder (use a paid API behind an interface, gated by score — wire later)
- Aggressive Instagram/Facebook scraping (ToS-brittle; use only public links)

---

## 3. Architecture

A batch pipeline, run per command invocation. State lives in SQLite so reruns are safe and dedup'd.

```
run(metros):
  for metro in metros:
    discover(metro)                  # Places API -> upsert businesses
  enrich(businesses where incomplete) # idempotent: only fills missing fields
  score(businesses)                   # deficiency score + primary deficiency
  enrich_contact(high scorers only)   # phone/form always; email via API if score warrants
  generate_hooks(high scorers)        # LLM, grounded in facts
  export_review_queue()               # ranked CSV + table
```

**Two principles that make "many metros, run whenever" manageable without an orchestrator:**

1. **Every stage is idempotent and self-healing.** A stage checks the DB; if a field is already present it skips; if missing it fetches and stores. A rerun naturally completes partial records and skips done ones. No queue needed.
2. **Partial failure never drops a lead.** If website-fetch times out, the business still has Places data and ships as a lower-confidence lead. Each enrichment field is independent.

---

## 4. Data model (SQLite)

Single file `leads.db`. One main table plus a lightweight event log for debugging.

```sql
CREATE TABLE businesses (
    place_id            TEXT PRIMARY KEY,   -- stable ID from Google Places (dedup key)
    name                TEXT NOT NULL,
    metro               TEXT NOT NULL,      -- which metro query found it
    address             TEXT,
    phone               TEXT,
    website_url         TEXT,               -- as reported by Places (may be linktree/fb/empty)
    rating              REAL,
    review_count        INTEGER,
    discovered_at       TEXT,               -- ISO timestamp

    -- enrichment: website analysis
    site_fetched_at     TEXT,               -- NULL = not yet attempted/succeeded
    site_fetch_status   TEXT,               -- 'ok' | 'timeout' | 'error' | 'no_site'
    site_platform       TEXT,               -- 'wix' | 'squarespace' | 'wordpress' | 'custom' | 'linktree' | 'facebook' | 'none' | 'unknown'
    has_ssl             INTEGER,            -- 0/1/NULL
    is_mobile_friendly  INTEGER,            -- 0/1/NULL (proxy: viewport meta + responsive hints)

    -- enrichment: booking
    booking_checked_at  TEXT,
    has_online_booking  INTEGER,            -- 0/1/NULL
    booking_platform    TEXT,               -- 'vagaro' | 'mindbody' | 'boulevard' | 'square' | 'acuity' | 'calendly' | 'other' | NULL

    -- enrichment: social
    social_checked_at   TEXT,
    instagram_url       TEXT,
    facebook_url        TEXT,
    social_last_post    TEXT,               -- ISO date if obtainable, else NULL
    social_status       TEXT,               -- 'active' | 'dormant' | 'none' | 'unknown'

    -- enrichment: contact
    owner_name          TEXT,
    email               TEXT,
    email_status        TEXT,               -- 'verified' | 'guessed' | 'not_found' | 'not_attempted'

    -- scoring
    scored_at           TEXT,
    deficiency_score    INTEGER,
    primary_deficiency  TEXT,               -- e.g. 'no_website' | 'no_booking' | 'not_mobile' | 'dormant_social' | 'reviews_vs_web'

    -- hook + workflow
    hook_text           TEXT,
    hook_generated_at   TEXT,
    review_status       TEXT DEFAULT 'new', -- 'new' | 'approved' | 'rejected' | 'contacted'
    notes               TEXT
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id    TEXT,
    stage       TEXT,       -- 'discover' | 'enrich_site' | 'enrich_booking' | ...
    level       TEXT,       -- 'info' | 'warn' | 'error'
    message     TEXT,
    created_at  TEXT
);
```

`NULL` in an enrichment timestamp = "stage not yet completed for this row" → that's the idempotency signal each stage keys on.

---

## 5. Scoring logic

Combine flags into an integer score; pick the highest-weighted present deficiency as `primary_deficiency` (that's what the hook references). **Weights below are placeholders — tune against real output, do not treat as final.**

```python
WEIGHTS = {
    "no_website":        3,   # site_platform in ('none','linktree','facebook')
    "no_booking":        2,   # has_online_booking == 0
    "not_mobile":        2,   # is_mobile_friendly == 0
    "no_ssl":            1,
    "dormant_social":    1,   # social_status == 'dormant'
    "no_social":         1,   # social_status == 'none'
}
# Bonus signal, applied on top:
#   reviews_vs_web: +2 if review_count >= 100 AND (no_website OR not_mobile OR no_booking)
#   -> "clearly busy, clearly leaking" — highest-converting hook archetype
```

`primary_deficiency` selection order (first match wins, by impact): `no_website` → `no_booking` → `not_mobile` → `reviews_vs_web` → `dormant_social`. The bonus can override to `reviews_vs_web` when present, since it's the strongest hook.

A "good lead" threshold gates expensive steps (email enrichment, hook gen). Start at `deficiency_score >= 4`; adjust once you see distribution.

---

## 6. Enrichment stages — interfaces & implementation notes

Each stage = a function over a business row that fills missing fields, is safe to rerun, logs to `events`, and never raises past a single row (catch, log, mark status, continue).

```python
from typing import Protocol

class EnrichmentStage(Protocol):
    name: str
    def needs_run(self, biz: dict) -> bool: ...   # True if this stage's fields are missing
    def run(self, biz: dict) -> dict: ...         # returns field updates; must not raise
```

### 6.1 Discovery (Google Places)
- Use Places **Text Search** / **Nearby Search**: `"med spa"`, `"day spa"`, `"medical spa"` × metro.
- Store `place_id` as PK → automatic dedup across overlapping queries and reruns.
- A missing/empty `website_url`, or one pointing at linktr.ee / facebook.com, is **already a deficiency signal** — capture it, don't discard the row.
- **Cache hard.** Places data barely changes; don't re-query a metro within N days. Cost control matters at many-metros scale.

### 6.2 Website analysis
- Fetch homepage (sane timeout, real User-Agent, follow redirects).
- **Platform detection:** look for fingerprints in HTML/headers — `wix.com` assets, `squarespace`, `wp-content`, Shopify, etc. Linktree/FB-only → `site_platform` set accordingly + `has_online_booking` likely 0.
- **Mobile-friendly proxy:** presence of `<meta name="viewport">` + absence of fixed-width layout hints. Cheap and good enough for PoC; don't run full Lighthouse yet.
- **SSL:** did the `https://` fetch succeed with a valid cert.
- On timeout/error: set `site_fetch_status`, leave the row usable.

### 6.3 Booking detection — *the one that may need a headless browser*
- First pass: scan fetched HTML for booking fingerprints (links/scripts: `vagaro`, `mindbodyonline`, `boulevard`/`joinblvd`, `squareup`/`square.site/book`, `acuityscheduling`, `calendly`).
- **Known gap:** many booking widgets inject via JS, so raw-HTML detection produces false negatives. **PoC decision:** ship with HTML-only detection first, measure false-negative rate on a hand-checked sample, and only add Playwright/headless rendering for this stage if the rate is unacceptable. Don't let headless-browser setup block first output.

### 6.4 Social signal (best-effort)
- Find IG/FB links from the website and Places.
- If reachable publicly, estimate last-post recency → `active` / `dormant`. If not, `unknown` — do **not** scrape aggressively or log into anything.
- This is the weakest/most-brittle signal; keep effort proportional.

### 6.5 Contact enrichment (gated by score)
- Phone/form contact: already have phone from Places; capture a contact-form URL if found on the site.
- **Email: behind an interface, stubbed for PoC.** Define the seam; wire a provider (Hunter / Apollo / Anymail Finder / Dropcontact) later. Only call it for `deficiency_score >= threshold` so credits are spent on real prospects.

```python
class EmailFinder(Protocol):
    def find(self, *, domain: str, business_name: str, owner_name: str | None) -> dict:
        """Returns {'email': str|None, 'status': 'verified'|'guessed'|'not_found'}."""
        ...

class StubEmailFinder:  # PoC default — does nothing, returns not_attempted
    def find(self, **kwargs): return {"email": None, "status": "not_attempted"}
```

---

## 7. Hook generation (the LLM step)

Runs only on score-qualified leads. The LLM receives **structured collected facts** and must produce one opening line that references a specific fact. The structured input is the guardrail against generic flattery / hallucination.

**Hard rule:** the hook must cite something we actually observed (platform, booking absence, review count, dormancy). No claim that isn't backed by a stored field.

Prompt shape (for the build agent to implement):

```
SYSTEM: You write a single, specific opening line for cold outreach to a med-spa owner.
You may ONLY reference facts provided in INPUT. Do not invent details. No generic
flattery. One or two sentences. Plain, human, lowercase-ok. State the observed gap
and imply the fix without hard-pitching.

INPUT (example):
  name: "Glow Med Spa"
  review_count: 612
  rating: 4.9
  site_platform: "wix"
  has_online_booking: false
  primary_deficiency: "reviews_vs_web"

GOOD output: "noticed glow has 600+ five-star reviews but no online booking on the
site — you're probably losing after-hours bookings to whoever picks up the phone first."

BAD output: "I love what you're doing at Glow Med Spa! I help spas grow!"  (generic, no fact)
```

Store result in `hook_text`. Human edits before sending — this is a draft, not a final.

---

## 8. Output: the review queue

Export qualified leads (`deficiency_score >= threshold`, `review_status='new'`), ranked by score desc:

- **CSV** for spreadsheet review: name, metro, score, primary_deficiency, phone, email, email_status, website_url, booking_platform, social_status, hook_text, source links.
- **Readable console table** for quick scanning.

The owner works the queue top-down: read hook, glance at the spa, edit, send via phone/form/email himself, mark `contacted`.

---

## 9. Suggested repo structure

```
medspa-leadgen/
├── README.md
├── pyproject.toml              # deps: requests, beautifulsoup4, python-dotenv
├── .env.example                # GOOGLE_PLACES_API_KEY, ANTHROPIC_API_KEY, EMAIL_API_KEY(optional)
├── cli.py                      # entry point: `python3 cli.py run --metros "Austin,TX;Dallas,TX"`
├── medspa_leads/               # main package
│   ├── __init__.py
│   ├── config.py               # metros list, scoring WEIGHTS, thresholds, cache TTLs
│   ├── db.py                   # SQLite connect, schema init, upsert/query helpers
│   ├── pipeline.py             # the run() orchestration in §3 (plain function, no daemon)
│   ├── export.py               # CSV + console table
│   └── stages/
│       ├── __init__.py
│       ├── discover.py         # Places API -> upsert
│       ├── enrich_site.py      # website fetch + platform/mobile/ssl
│       ├── enrich_booking.py   # booking fingerprint detection (HTML-first)
│       ├── enrich_social.py    # best-effort social signal
│       ├── enrich_contact.py   # phone/form + EmailFinder interface (stub default)
│       ├── score.py            # deficiency_score + primary_deficiency
│       └── hooks.py            # LLM hook generation (grounded)
└── tests/
    ├── test_score.py           # scoring is pure logic -> test it thoroughly
    ├── test_booking_detect.py  # fingerprint detection on saved HTML fixtures
    ├── test_integration.py     # full pipeline E2E tests
    └── fixtures/               # saved sample pages for offline testing
```

---

## 10. Build order (each step independently runnable)

1. `db.py` + schema + `events` logging. Verify upsert/dedup on `place_id`.
2. `discover.py` → real Places data landing in DB for one metro.
3. `score.py` (pure function) + `test_score.py`. Scoreable on Places-only data (catches no-website cases immediately).
4. `export.py` → you already have a usable (if shallow) ranked list. **First real output here.**
5. `enrich_site.py` → platform/mobile/ssl deepen the score.
6. `enrich_booking.py` (HTML-first). Hand-check false-negative rate before deciding on headless.
7. `hooks.py` → grounded drafts on qualified leads.
8. `enrich_social.py` (best-effort), `enrich_contact.py` email wiring — last, lowest-leverage / most-brittle.

By step 4 there's something the owner can look at and react to. Get to step 4 fast; treat 5–8 as iterative deepening informed by real output.

---

## 11. Things to watch (failure modes, stated plainly)

- **Email finding is the time sink.** It's walled off behind an interface for exactly this reason. Resist building your own.
- **Per-site fetching is brittle.** Timeouts, JS-rendered content, bot-blocks are normal. Design for partial records, never all-or-nothing.
- **Volume is small per metro** (≈50–200 spas; ≈20–40 good leads). This is a "do 30 well" problem, not a scale problem — don't over-build infra.
- **Scoring weights are guesses until calibrated.** Run a metro, eyeball the top 20, adjust weights, repeat. The weights are the product; tune them on real data.
- **Compliance:** sending stays manual and from the owner's own identity, so there's no account-ban surface. If email is added, follow CAN-SPAM (identification + opt-out); for any EU contacts, GDPR/PECR consent rules apply.
```