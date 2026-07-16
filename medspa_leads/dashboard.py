"""Global competitor scraping batch for the benchmark dashboard.

The batch discovers explicit metros or every distinct tenant primary-location
metro, stores global competitor observations, and synchronizes tracked links
only for tenant-backed markets.

Usage:
    python3 cli.py dashboard-scrape
    python3 cli.py dashboard-scrape --metro "Chicago, IL"
    python3 cli.py dashboard-scrape --metro "Chicago, IL" --metro "Milwaukee, WI" --force
"""

import datetime
import hashlib
from pathlib import Path
import sys
from urllib.parse import urlparse
from collections.abc import Iterable

import requests
from bs4 import BeautifulSoup
from supabase import Client, create_client

from medspa_leads import config
from medspa_leads.competitor_pricing import PageDocument, crawl_competitor
from medspa_leads.stages.enrich_booking import USER_AGENT, detect_booking_platform
from medspa_leads.stages.enrich_site import detect_platform, is_mobile_friendly_proxy
from medspa_leads.stages.enrich_social import find_social_links


MARKET_CACHE_DAYS = 7


# ---------------------------------------------------------------------------
# Supabase and market helpers
# ---------------------------------------------------------------------------

def get_supabase() -> Client:
    """Create a Supabase client from config vars. Exit if unconfigured."""
    if not config.SUPABASE_URL or not config.SUPABASE_SECRET_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SECRET_KEY must be set in .env")
        sys.exit(1)
    return create_client(config.SUPABASE_URL, config.SUPABASE_SECRET_KEY)


def require_local_mock_target() -> None:
    """Fixture crawls must never write mock competitors to a remote project."""
    hostname = urlparse(config.SUPABASE_URL).hostname
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("--mock only supports a local Supabase URL; use integration checks for remote fixture coverage.")


def normalize_metro(metro: str) -> tuple[str, str]:
    """Return display and case-insensitive keys for a normalized metro string."""
    display = " ".join(metro.split())
    if not display:
        raise ValueError("metro is blank")
    return display, display.casefold()


def primary_market_tenants(supabase: Client) -> dict[str, dict[str, object]]:
    """Group primary-location tenants by their normalized metro key."""
    locations = (
        supabase.table("locations")
        .select("tenant_id, metro")
        .eq("is_primary", True)
        .not_.is_("metro", "null")
        .execute()
        .data
    )

    markets: dict[str, dict[str, object]] = {}
    invalid_tenants: list[str] = []
    for location in locations:
        try:
            metro, metro_key = normalize_metro(location["metro"])
        except (AttributeError, ValueError):
            invalid_tenants.append(location["tenant_id"])
            continue

        market = markets.setdefault(metro_key, {"metro": metro, "tenant_ids": []})
        market["tenant_ids"].append(location["tenant_id"])

    if invalid_tenants:
        raise ValueError(
            "Primary locations with blank metros: " + ", ".join(invalid_tenants)
        )
    if not markets:
        raise ValueError("No primary-location metros are configured.")
    return markets


def explicit_markets(metros: Iterable[str]) -> dict[str, dict[str, object]]:
    """Build market-only discovery targets without inventing tenant tracking links."""
    markets: dict[str, dict[str, object]] = {}
    for raw_metro in metros:
        metro, metro_key = normalize_metro(raw_metro)
        markets.setdefault(metro_key, {"metro": metro, "tenant_ids": []})
    if not markets:
        raise ValueError("At least one --metro value is required.")
    return markets


def market_is_fresh(supabase: Client, metro_key: str) -> bool:
    """Return whether a market discovery completed within the cache window."""
    rows = (
        supabase.table("competitor_market_scrapes")
        .select("last_completed_at, last_status")
        .eq("metro_key", metro_key)
        .limit(1)
        .execute()
        .data
    )
    if not rows or rows[0].get("last_status") != "complete" or not rows[0].get("last_completed_at"):
        return False
    completed_at = datetime.datetime.fromisoformat(rows[0]["last_completed_at"].replace("Z", "+00:00"))
    return (datetime.datetime.now(datetime.timezone.utc) - completed_at).days < MARKET_CACHE_DAYS


def mark_market_status(
    supabase: Client, metro: str, metro_key: str, status: str, completed_at: str | None, error: str | None = None,
) -> None:
    """Persist compact market-discovery state rather than a historical run log."""
    supabase.table("competitor_market_scrapes").upsert(
        {
            "metro": metro,
            "metro_key": metro_key,
            "last_status": status,
            "last_completed_at": completed_at,
            "last_error": error,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        on_conflict="metro_key",
    ).execute()


def market_competitor_ids(supabase: Client, metro_key: str) -> set[str]:
    """Return the global competitors observed in a market."""
    rows = (
        supabase.table("competitor_markets")
        .select("competitor_id")
        .eq("metro_key", metro_key)
        .execute()
        .data
    )
    return {row["competitor_id"] for row in rows}


# ---------------------------------------------------------------------------
# Global competitor persistence
# ---------------------------------------------------------------------------

def save_competitor(
    supabase: Client,
    name: str,
    place_id: str,
    website: str | None,
    existing_id: str | None = None,
    rating: float | None = None,
    review_count: int | None = None,
) -> str:
    """Insert or refresh compact global competitor metadata."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload = {
        "name": name,
        "place_id": place_id,
        "website": website,
        "rating": rating,
        "review_count": review_count,
        "reviews_checked_at": now_iso,
        "last_discovered_at": now_iso,
        "updated_at": now_iso,
    }
    if existing_id:
        supabase.table("competitors").update(payload).eq("id", existing_id).execute()
        return existing_id
    result = supabase.table("competitors").insert(payload).execute()
    return result.data[0]["id"]


def record_competitor_market(
    supabase: Client, competitor_id: str, metro: str, metro_key: str, seen_at: str
) -> None:
    """Persist a global competitor's membership in a discovered market."""
    supabase.table("competitor_markets").upsert(
        {
            "competitor_id": competitor_id,
            "metro": metro,
            "metro_key": metro_key,
            "last_seen_at": seen_at,
        },
        on_conflict="competitor_id,metro_key",
    ).execute()




# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_market(supabase: Client, metro: str, metro_key: str, force: bool = False) -> set[str]:
    """Discover one market once and return its global competitor IDs."""
    if not force and market_is_fresh(supabase, metro_key):
        print(f"  {metro}: cached within {MARKET_CACHE_DAYS} days; skipping discovery.")
        return market_competitor_ids(supabase, metro_key)

    if not config.GOOGLE_PLACES_API_KEY:
        raise ValueError("GOOGLE_PLACES_API_KEY is required for real discovery.")

    queries = [f"med spa in {metro}", f"day spa in {metro}", f"medical spa in {metro}"]
    results_by_place_id: dict[str, dict] = {}
    completed = True

    for query in queries:
        try:
            response = requests.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": query, "key": config.GOOGLE_PLACES_API_KEY},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") not in ("OK", "ZERO_RESULTS"):
                raise RuntimeError(payload.get("error_message") or payload.get("status"))
            for result in payload.get("results", []):
                place_id = result.get("place_id")
                if place_id:
                    results_by_place_id[place_id] = result
        except Exception as e:
            completed = False
            print(f"  {metro}: search query failed ({query}): {e}")

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    competitor_ids: set[str] = set()

    for place_id, result in results_by_place_id.items():
        name = result.get("name")
        address = result.get("formatted_address")
        rating = result.get("rating")
        review_count = result.get("user_ratings_total", 0)

        try:
            existing_rows = (
                supabase.table("competitors")
                .select("id, website")
                .eq("place_id", place_id)
                .limit(1)
                .execute()
                .data
            )
            existing = existing_rows[0] if existing_rows else None
            website = existing["website"] if existing else None

            if not existing:
                try:
                    details_response = requests.get(
                        "https://maps.googleapis.com/maps/api/place/details/json",
                        params={
                            "place_id": place_id,
                            "fields": "formatted_phone_number,website",
                            "key": config.GOOGLE_PLACES_API_KEY,
                        },
                        timeout=10,
                    )
                    details_response.raise_for_status()
                    details_payload = details_response.json()
                    if details_payload.get("status") == "OK":
                        website = details_payload.get("result", {}).get("website")
                except Exception as e:
                    print(f"  Warning: Place Details failed for {name}: {e}")

            competitor_id = save_competitor(
                supabase,
                name,
                place_id,
                website,
                existing["id"] if existing else None,
                rating,
                review_count,
            )
            record_competitor_market(supabase, competitor_id, metro, metro_key, now_iso)
            competitor_ids.add(competitor_id)
            print(f"  {name} (rating={rating}, reviews={review_count})")
        except Exception as e:
            completed = False
            print(f"  Warning: competitor write failed for {name}: {e}")

    if completed:
        mark_market_status(supabase, metro, metro_key, "complete", now_iso)
    else:
        mark_market_status(supabase, metro, metro_key, "partial", None, "One or more Google Places queries or writes failed.")
        print(f"  {metro}: incomplete discovery was not cached.")

    return competitor_ids


MOCK_COMPETITORS = [
    {"name": "Glow Day Spa", "place_id": "mock_place_001", "website": None, "rating": 4.2, "review_count": 85},
    {"name": "Serenity MedSpa", "place_id": "mock_place_002", "website": "https://serenityspa-mock.com", "rating": 4.7, "review_count": 203},
    {"name": "Luxe Aesthetics", "place_id": "mock_place_003", "website": "https://luxeaesthetics-mock.com", "rating": 4.9, "review_count": 156},
    {"name": "Bloom Wellness", "place_id": "mock_place_004", "website": "https://bloomwellness-mock.com", "rating": 3.8, "review_count": 52},
    {"name": "Elite Skin Studio", "place_id": "mock_place_005", "website": "https://eliteskin-mock.com", "rating": 4.5, "review_count": 298},
]


def discover_mock_market(supabase: Client, metro: str, metro_key: str, force: bool = False) -> set[str]:
    """Write deterministic global fixtures for one primary market."""
    if not force and market_is_fresh(supabase, metro_key):
        print(f"  {metro}: cached within {MARKET_CACHE_DAYS} days; skipping mock discovery.")
        return market_competitor_ids(supabase, metro_key)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    competitor_ids: set[str] = set()
    for mock in MOCK_COMPETITORS:
        existing_rows = (
            supabase.table("competitors")
            .select("id")
            .eq("place_id", mock["place_id"])
            .limit(1)
            .execute()
            .data
        )
        existing = existing_rows[0] if existing_rows else None
        competitor_id = save_competitor(
            supabase,
            mock["name"],
            mock["place_id"],
            mock["website"],
            existing["id"] if existing else None,
            mock["rating"],
            mock["review_count"],
        )
        record_competitor_market(supabase, competitor_id, metro, metro_key, now_iso)
        competitor_ids.add(competitor_id)

    mark_market_status(supabase, metro, metro_key, "complete", now_iso)
    print(f"  {metro}: [Mock] observed {len(MOCK_COMPETITORS)} competitors")
    return competitor_ids


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def enrich_competitors(supabase: Client, competitor_ids: Iterable[str]) -> int:
    """Enrich the unique global competitors discovered in this batch."""
    ids = sorted(set(competitor_ids))
    if not ids:
        return 0

    competitors = (
        supabase.table("competitors")
        .select("id, name, website, ig_handle")
        .in_("id", ids)
        .not_.is_("website", "null")
        .execute()
        .data
    )

    enriched = 0
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for comp in competitors:
        competitor_id = comp["id"]
        name = comp["name"]
        try:
            response = requests.get(
                comp["website"],
                headers={"User-Agent": USER_AGENT},
                timeout=15,
                allow_redirects=True,
            )
            response.raise_for_status()
        except Exception as e:
            print(f"  {name}: fetch failed — {e}")
            continue

        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        has_booking, booking_platform = detect_booking_platform(html, soup)
        instagram_url, facebook_url = find_social_links(html, soup)
        site_platform = detect_platform(html, dict(response.headers), str(response.url))
        mobile = is_mobile_friendly_proxy(soup)
        has_ssl = str(response.url).startswith("https://")

        ig_handle = comp.get("ig_handle")
        if instagram_url and not ig_handle:
            try:
                ig_handle = instagram_url.split("instagram.com/")[1].rstrip("/").split("?")[0]
            except (IndexError, KeyError):
                pass
        supabase.table("competitors").update(
            {
                "booking_platform": booking_platform,
                "website_checked_at": now_iso,
                "ig_handle": ig_handle,
                "updated_at": now_iso,
            }
        ).eq("id", competitor_id).execute()
        enriched += 1
        ig_handle = (
            comp.get("ig_handle")
            or (instagram_url and instagram_url.split("instagram.com/")[-1].rstrip("/").split("?")[0])
            or "none"
        )
        print(f"  {name}: booking={booking_platform or 'none'}, ig={ig_handle}, platform={site_platform}")

    return enriched


MOCK_ENRICHMENT = {
    "mock_place_002": ("none", "https://instagram.com/serenityspa_mock", "https://facebook.com/serenityspa", "wix"),
    "mock_place_003": ("boulevard", "https://instagram.com/luxeaesthetics", None, "wordpress"),
    "mock_place_004": ("vagaro", None, "https://facebook.com/bloomwellness", "squarespace"),
    "mock_place_005": ("mindbody", "https://instagram.com/eliteskin_mock", "https://facebook.com/eliteskin", "custom"),
}


def enrich_mock(supabase: Client, competitor_ids: Iterable[str]) -> int:
    """Write deterministic enrichment snapshots once per global mock competitor."""
    ids = sorted(set(competitor_ids))
    if not ids:
        return 0

    competitors = (
        supabase.table("competitors")
        .select("id, name, place_id, website")
        .in_("id", ids)
        .not_.is_("website", "null")
        .execute()
        .data
    )
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    enriched = 0

    for comp in competitors:
        details = MOCK_ENRICHMENT.get(comp["place_id"])
        if not details:
            continue
        booking_platform, ig_url, fb_url, site_platform = details
        ig_handle = ig_url.split("instagram.com/")[1].rstrip("/").split("?")[0] if ig_url else None
        supabase.table("competitors").update(
            {
                "booking_platform": booking_platform if booking_platform != "none" else None,
                "website_checked_at": now_iso,
                "ig_handle": ig_handle,
                "updated_at": now_iso,
            }
        ).eq("id", comp["id"]).execute()
        enriched += 1
        print(f"  {comp['name']}: booking={booking_platform}, ig={ig_url.split('instagram.com/')[1] if ig_url else 'none'}, platform={site_platform}")

    return enriched

def mock_pricing_documents() -> dict[str, list[PageDocument]]:
    """Load local website fixtures into the identical crawl persistence path."""
    fixture_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pricing"
    fixtures = {
        "mock_place_002": ("static-menu.html", "https://serenityspa-mock.com/services"),
        "mock_place_003": ("rendered-menu.html", "https://luxeaesthetics-mock.com/treatments"),
        "mock_place_004": ("membership-package.html", "https://bloomwellness-mock.com/memberships"),
        "mock_place_005": ("offer.html", "https://eliteskin-mock.com/offers"),
    }
    documents: dict[str, list[PageDocument]] = {}
    for place_id, (filename, url) in fixtures.items():
        html = (fixture_dir / filename).read_text()
        soup = BeautifulSoup(html, "html.parser")
        visible = " ".join(soup.stripped_strings)
        documents[place_id] = [PageDocument(
            url=url,
            final_url=url,
            title=soup.title.get_text(" ", strip=True) if soup.title else None,
            html=html,
            visible_text=visible,
            content_sha256=hashlib.sha256(html.encode()).hexdigest(),
            http_status=200,
            render_mode="browser" if filename == "rendered-menu.html" else "http",
        )]
    return documents


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

def run_dashboard_scrape(
    mock: bool = False,
    force: bool = False,
    metros: Iterable[str] | None = None,
) -> None:
    """Discover requested metros and crawl every discovered competitor website."""
    if mock:
        require_local_mock_target()
    supabase = get_supabase()
    try:
        markets = explicit_markets(metros) if metros is not None else primary_market_tenants(supabase)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    source = "explicit metro override" if metros is not None else "tenant primary locations"
    print(f"Dashboard Competitor Scrape — {len(markets)} market(s) from {source}")
    print("--- Stage 1: Discovery ---")
    discovered_ids: set[str] = set()
    for metro_key, market in sorted(markets.items()):
        metro = market["metro"]
        if mock:
            discovered_ids.update(discover_mock_market(supabase, metro, metro_key, force))
        else:
            discovered_ids.update(discover_market(supabase, metro, metro_key, force))

    print("--- Stage 2: Website enrichment ---")
    n_enriched = (
        enrich_mock(supabase, discovered_ids)
        if mock
        else enrich_competitors(supabase, discovered_ids)
    )

    print("--- Stage 3: Pricing crawl ---")
    competitors = (
        supabase.table("competitors")
        .select("id, name, place_id, website")
        .in_("id", sorted(discovered_ids))
        .not_.is_("website", "null")
        .execute()
        .data
        if discovered_ids
        else []
    )
    mock_documents = mock_pricing_documents() if mock else None
    print(f"  Crawling {len(competitors)} discovered competitor website(s).")
    crawl_results = []
    for index, competitor in enumerate(competitors, start=1):
        print(f"  Pricing progress: competitor {index}/{len(competitors)}.")
        crawl_results.append(crawl_competitor(supabase, competitor, force=force, mock_documents=mock_documents))
    n_crawled = sum(status is not None for status in crawl_results)
    print(
        f"Done. {len(discovered_ids)} global competitors discovered, "
        f"{n_enriched} enriched, {n_crawled} website crawls."
    )
