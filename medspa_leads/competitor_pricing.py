"""Source-backed competitor website pricing crawler.

The module deliberately owns no global database connection.  Pure discovery,
fetching, extraction, and validation functions are testable with fixture HTML;
`crawl_competitor` is the narrow persistence adapter used by dashboard.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
import unicodedata
import urllib.robotparser
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
import tldextract
from bs4 import BeautifulSoup

from medspa_leads import config
from medspa_leads.stages.enrich_booking import BOOKING_SIGNATURES, USER_AGENT

MAX_PAGES = 12
MAX_BODY_BYTES = 5 * 1024 * 1024
HTTP_TIMEOUT = 20
CRAWL_FRESH_DAYS = 7
PRICE_LINK_WORDS = (
    "price", "pricing", "service", "treatment", "menu", "membership", "package",
    "special", "offer", "promo", "book", "appointment",
)
OFFERING_TYPES = Literal["service", "membership", "package", "offer"]
PRICE_TYPES = Literal["none", "fixed", "from", "range"]
PRICE_UNITS = Literal["total", "unit", "session", "month", "package"]

# The packaged PSL snapshot must be used: no network suffix-list update during a crawl.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_LAST_REQUEST_AT: dict[str, float] = {}


@dataclass(frozen=True)
class PageDocument:
    url: str
    final_url: str
    title: str | None
    html: str
    visible_text: str
    content_sha256: str
    http_status: int
    render_mode: Literal["http", "browser"]


@dataclass(frozen=True)
class OfferingCandidate:
    offering_type: OFFERING_TYPES
    display_name: str
    qualifier: str | None
    price_type: PRICE_TYPES
    amount_min: Decimal | None
    amount_max: Decimal | None
    currency: str | None
    price_unit: PRICE_UNITS | None
    package_quantity: int | None
    original_amount: Decimal | None
    valid_from: dt.date | None
    valid_through: dt.date | None
    price_display: str | None
    evidence_text: str
    source_url: str


@dataclass(frozen=True)
class ValidatedObservation:
    candidate: OfferingCandidate
    offering_key: str
    source_fingerprint: str
    publication_status: Literal["pending", "published"]
    review_reason: str | None
    service_catalog_id: str | None


def normalize_text(value: str | None) -> str:
    """Normalize labels for stable aliases, fingerprints, and evidence checks."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold().replace("&", " and ")
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(normalized.split())


def canonical_url(url: str) -> str:
    """Drop fragments and tracking parameters while preserving booking identifiers."""
    parsed = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith(("utm_", "fbclid", "gclid"))]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", urlencode(query), ""))


def registrable_host(url: str) -> str:
    parts = _EXTRACT(url)
    return ".".join(part for part in (parts.domain, parts.suffix) if part)


def _booking_hosts() -> set[str]:
    return {registrable_host(f"https://{signature.split('/')[0]}")
            for signatures in BOOKING_SIGNATURES.values() for signature in signatures}


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()
    return " ".join(soup.stripped_strings)


def _request_allowed(url: str, session: requests.Session) -> bool:
    parsed = urlparse(url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    try:
        response = session.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        if response.status_code >= 400:
            return True
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        # An unavailable robots endpoint is not a denial.
        return True


def _rate_limit(url: str) -> None:
    host = urlparse(url).netloc.lower()
    remaining = 1 - (time.monotonic() - _LAST_REQUEST_AT.get(host, 0))
    if remaining > 0:
        time.sleep(remaining)
    _LAST_REQUEST_AT[host] = time.monotonic()


def fetch_page(url: str, session: requests.Session | None = None) -> PageDocument:
    """Fetch one HTML page after robots and scope checks, enforcing request limits."""
    session = session or requests.Session()
    url = canonical_url(url)
    if not _request_allowed(url, session):
        raise RuntimeError("robots_denied")
    _rate_limit(url)
    response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT,
                           allow_redirects=True, stream=True)
    if response.status_code >= 400:
        raise RuntimeError(f"http_{response.status_code}")
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        raise RuntimeError("non_html")
    chunks: list[bytes] = []
    length = 0
    for chunk in response.iter_content(chunk_size=65536):
        length += len(chunk)
        if length > MAX_BODY_BYTES:
            raise RuntimeError("oversized_body")
        chunks.append(chunk)
    html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    return PageDocument(url, canonical_url(str(response.url)), soup.title.get_text(" ", strip=True) if soup.title else None,
                        html, _visible_text(html), hashlib.sha256(html.encode()).hexdigest(), response.status_code, "http")


def render_page(url: str) -> PageDocument:
    """Render a high-signal JS shell only after the requests-first fetch proves sparse."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.set_default_navigation_timeout(30_000)
        response = page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3_000)
        html = page.content()
        final_url = canonical_url(page.url)
        title = page.title() or None
        status = response.status if response else 200
        browser.close()
    return PageDocument(url, final_url, title, html, _visible_text(html), hashlib.sha256(html.encode()).hexdigest(), status, "browser")


def _is_high_signal(url: str) -> bool:
    path = urlparse(url).path.casefold()
    return any(word in path for word in PRICE_LINK_WORDS)


def _needs_render(document: PageDocument) -> bool:
    compact = document.html.casefold()
    return len(document.visible_text) < 200 or ("id=\"root\"" in compact and not extract_candidates(document))


def discover_urls(homepage: PageDocument, sitemap_xml: str | None = None) -> list[str]:
    """Select homepage plus at most eleven scored in-scope links, depth one."""
    root = registrable_host(homepage.final_url)
    links: dict[str, int] = {canonical_url(homepage.final_url): 10_000}
    soup = BeautifulSoup(homepage.html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        absolute = canonical_url(urljoin(homepage.final_url, anchor["href"]))
        host = registrable_host(absolute)
        allowed_booking = host in _booking_hosts()
        if host != root and not allowed_booking:
            continue
        label = f"{anchor.get_text(' ', strip=True)} {absolute}".casefold()
        score = sum(word in label for word in PRICE_LINK_WORDS)
        if score:
            links[absolute] = max(links.get(absolute, 0), score)
    if sitemap_xml:
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", sitemap_xml, flags=re.I | re.S):
            absolute = canonical_url(loc)
            if registrable_host(absolute) == root:
                score = sum(word in absolute.casefold() for word in PRICE_LINK_WORDS)
                if score:
                    links[absolute] = max(links.get(absolute, 0), score)
    selected = sorted(links, key=lambda link: (-links[link], link))
    homepage_url = canonical_url(homepage.final_url)
    return [homepage_url] + [url for url in selected if url != homepage_url][: MAX_PAGES - 1]


def _currency_and_amount(raw: str) -> tuple[Decimal | None, str | None]:
    match = re.search(r"(?P<currency>\$|USD|US\$|CAD|C\$|€|£)\s?(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)", raw, flags=re.I)
    if not match:
        return None, None
    currency_token = match.group("currency").upper()
    currency = "USD" if currency_token in {"$", "USD", "US$"} else None
    try:
        return Decimal(match.group("amount").replace(",", "")), currency
    except InvalidOperation:
        return None, currency


def _candidate_from_text(text: str, url: str) -> OfferingCandidate | None:
    """Parse a single repeated menu/offer row without inferring absent values."""
    clean = " ".join(text.split())
    amount, currency = _currency_and_amount(clean)
    name_match = re.match(r"([A-Za-z][A-Za-z0-9&/'\- ]{1,100}?)(?:\s*(?:[-–—|:]|\$|from\b|starting\b))", clean, flags=re.I)
    name = name_match.group(1).strip(" -–—|:") if name_match else ""
    if not name:
        return None
    lower = clean.casefold()
    offering_type: OFFERING_TYPES = "offer" if any(term in lower for term in ("offer", "special", "expires", "valid through")) else (
        "membership" if "membership" in lower else "package" if "package" in lower else "service"
    )
    price_type: PRICE_TYPES = "none"
    amount_max: Decimal | None = None
    if amount is not None:
        range_match = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)\s*(?:-|–|to)\s*\$\s*([\d,]+(?:\.\d{1,2})?)", clean)
        if range_match:
            price_type, amount, amount_max = "range", Decimal(range_match.group(1).replace(",", "")), Decimal(range_match.group(2).replace(",", ""))
        elif re.search(r"\b(from|starting at)\b", lower):
            price_type = "from"
        else:
            price_type = "fixed"
    unit: PRICE_UNITS | None = "month" if re.search(r"/(?:mo|month)\b|per month", lower) else (
        "session" if re.search(r"per session|/session", lower) else "unit" if re.search(r"per unit|/unit", lower) else "package" if "package" in lower else "total" if amount else None
    )
    quantity_match = re.search(r"\b(\d+)\s*(?:sessions|treatments)\b", lower)
    promo_match = re.search(r"\bwas\s+(\$\s*[\d,]+(?:\.\d{1,2})?).{0,40}?\bnow\s+(\$\s*[\d,]+(?:\.\d{1,2})?)", clean, re.I)
    original, _ = _currency_and_amount(promo_match.group(1)) if promo_match else (None, None)
    if promo_match:
        amount, currency = _currency_and_amount(promo_match.group(2))
    date_matches = re.findall(r"\b(?:through|until|valid)\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})", clean, re.I)
    valid_through = None
    if date_matches:
        for format_string in ("%B %d, %Y", "%B %d %Y", "%Y-%m-%d"):
            try:
                valid_through = dt.datetime.strptime(date_matches[-1], format_string).date()
                break
            except ValueError:
                continue
    price_display = re.search(r"(?:from\s+|starting at\s+|now\s+)?\$\s*[\d,]+(?:\.\d{1,2})?(?:\s*(?:-|–|to)\s*\$\s*[\d,]+(?:\.\d{1,2})?)?", clean, re.I)
    if promo_match:
        price_display = re.search(r"now\s+\$\s*[\d,]+(?:\.\d{1,2})?", clean, re.I)
    return OfferingCandidate(offering_type, name, None, price_type, amount, amount_max, currency, unit,
                             int(quantity_match.group(1)) if quantity_match else None, original, None, valid_through,
                             price_display.group(0) if price_display else None, clean[:500], url)


def _walk_jsonld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)
    elif isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_jsonld(nested)


def extract_candidates(document: PageDocument) -> list[OfferingCandidate]:
    """Extract JSON-LD Offers then deterministic visible repeated price rows."""
    candidates: list[OfferingCandidate] = []
    soup = BeautifulSoup(document.html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_jsonld(payload):
            if node.get("@type") not in ("Product", "Service", "Offer") and "offers" not in node:
                continue
            offers = node.get("offers", node if node.get("@type") == "Offer" else [])
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                name = str(node.get("name") or offer.get("name") or "").strip()
                price = offer.get("price") or offer.get("lowPrice")
                if not name or price is None:
                    continue
                try:
                    amount = Decimal(str(price))
                except InvalidOperation:
                    continue
                display = f"{offer.get('priceCurrency', 'USD')} {price}"
                candidates.append(OfferingCandidate("service", name, None, "fixed", amount, None,
                    str(offer.get("priceCurrency") or "USD").upper(), "total", None, None, None, None,
                    display, f"{name} {display}", document.final_url))
    seen: set[tuple[str, str | None]] = {(normalize_text(c.display_name), c.price_display) for c in candidates}
    for element in soup.find_all(["li", "tr", "article", "section", "div", "p"]):
        text = " ".join(element.stripped_strings)
        if "$" not in text or len(text) > 500:
            continue
        candidate = _candidate_from_text(text, document.final_url)
        if candidate and (normalize_text(candidate.display_name), candidate.price_display) not in seen:
            candidates.append(candidate)
            seen.add((normalize_text(candidate.display_name), candidate.price_display))
    return candidates


def offering_key(candidate: OfferingCandidate) -> str:
    return ":".join((candidate.offering_type, normalize_text(candidate.display_name),
                     normalize_text(candidate.qualifier) or "base", candidate.price_unit or "none",
                     str(candidate.package_quantity or 0)))


def _minor(amount: Decimal | None) -> int | None:
    return int((amount * 100).to_integral_value()) if amount is not None else None


def validate_candidate(candidate: OfferingCandidate, competitor_id: str, market: str | None,
                       aliases: dict[str, str] | None = None, catalog_names: dict[str, str] | None = None,
                       booking_branded: bool = True) -> ValidatedObservation:
    """Apply evidence-only publication policy; every valid candidate remains retained."""
    aliases, catalog_names = aliases or {}, catalog_names or {}
    key = offering_key(candidate)
    normalized_evidence = normalize_text(candidate.evidence_text)
    normalized_name = normalize_text(candidate.display_name)
    reason: str | None = None
    if not booking_branded:
        reason = "unverified_booking_brand"
    elif normalized_name not in normalized_evidence:
        reason = "name_not_in_evidence"
    elif candidate.price_type != "none":
        if not candidate.price_display or normalize_text(candidate.price_display) not in normalized_evidence:
            reason = "amount_not_in_evidence"
        elif candidate.currency is None:
            if market and re.fullmatch(r"[^,]+,\s*[A-Z]{2}", market):
                candidate = OfferingCandidate(**{**candidate.__dict__, "currency": "USD"})
            else:
                reason = "unknown_currency"
        elif candidate.price_type == "range" and candidate.amount_max is None:
            reason = "ambiguous_price_semantics"
    catalog_id = aliases.get(normalized_name) or catalog_names.get(normalized_name)
    if candidate.price_type == "none" and not catalog_id and reason is None:
        reason = "service_mapping_needed"
    fingerprint = hashlib.sha256("|".join(map(str, (
        competitor_id, canonical_url(candidate.source_url), key, candidate.price_type, candidate.amount_min,
        candidate.amount_max, candidate.currency, candidate.price_unit, candidate.package_quantity,
        candidate.original_amount, candidate.valid_from, candidate.valid_through, normalized_evidence,
    ))).encode()).hexdigest()
    return ValidatedObservation(candidate, key, fingerprint, "published" if reason is None else "pending", reason, catalog_id)


def unique_observations(
    observations: Iterable[ValidatedObservation],
    seen: set[str] | None = None,
) -> list[ValidatedObservation]:
    """Keep first-seen source facts; repeated DOM containers must not duplicate inserts."""
    seen_fingerprints = seen if seen is not None else set()
    unique: list[ValidatedObservation] = []
    for observation in observations:
        if observation.source_fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(observation.source_fingerprint)
        unique.append(observation)
    return unique


def offering_values(
    observation: ValidatedObservation,
    competitor_id: str,
    evidence_page_id: str | None,
    observed_at: str,
) -> dict[str, Any]:
    candidate = observation.candidate
    return {
        "competitor_id": competitor_id,
        "evidence_page_id": evidence_page_id,
        "offering_key": observation.offering_key,
        "offering_type": candidate.offering_type,
        "display_name": candidate.display_name,
        "qualifier": candidate.qualifier,
        "service_catalog_id": observation.service_catalog_id,
        "price_type": candidate.price_type,
        "amount_min_minor": _minor(candidate.amount_min),
        "amount_max_minor": _minor(candidate.amount_max),
        "currency": candidate.currency,
        "price_unit": candidate.price_unit,
        "package_quantity": candidate.package_quantity,
        "original_amount_minor": _minor(candidate.original_amount),
        "valid_from": candidate.valid_from.isoformat() if candidate.valid_from else None,
        "valid_through": candidate.valid_through.isoformat() if candidate.valid_through else None,
        "price_display": candidate.price_display,
        "source_url": candidate.source_url,
        "evidence_text": candidate.evidence_text,
        "source_fingerprint": observation.source_fingerprint,
        "last_seen_at": observed_at,
    }


def crawl_is_fresh(supabase: Any, competitor_id: str) -> bool:
    rows = (supabase.table("competitor_crawl_runs").select("completed_at").eq("competitor_id", competitor_id)
            .in_("status", ["complete", "partial"]).order("completed_at", desc=True).limit(1).execute().data)
    if not rows or not rows[0].get("completed_at"):
        return False
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(rows[0]["completed_at"].replace("Z", "+00:00"))).days < CRAWL_FRESH_DAYS


def _archive(supabase: Any, path: str, html: str) -> None:
    supabase.storage.from_("competitor-crawl-archive").upload(
        path, html.encode(), {"content-type": "text/html", "upsert": "false"}
    )


def persist_published_offering(
    supabase: Any, observation: ValidatedObservation, competitor_id: str,
    evidence_page_id: str, observed_at: str,
) -> None:
    """Upsert one current fact and create one global event only for a real change."""
    payload = offering_values(observation, competitor_id, evidence_page_id, observed_at)
    existing_rows = (supabase.table("competitor_offerings").select("*")
                     .eq("competitor_id", competitor_id).eq("offering_key", observation.offering_key)
                     .limit(1).execute().data)
    existing = existing_rows[0] if existing_rows else None
    material_fields = (
        "offering_type", "display_name", "qualifier", "service_catalog_id", "price_type",
        "amount_min_minor", "amount_max_minor", "currency", "price_unit", "package_quantity",
        "original_amount_minor", "valid_from", "valid_through", "price_display",
    )
    changed = existing is not None and any(existing.get(field) != payload.get(field) for field in material_fields)
    if existing:
        supabase.table("competitor_offerings").update({
            **payload, "updated_at": observed_at,
        }).eq("id", existing["id"]).execute()
        offering_id = existing["id"]
    else:
        saved = supabase.table("competitor_offerings").insert({
            **payload, "first_published_at": observed_at, "updated_at": observed_at,
        }).execute().data[0]
        offering_id = saved["id"]

    if existing is None:
        event_type = "competitor_offer_added" if observation.candidate.offering_type == "offer" else "competitor_offering_added"
    elif changed:
        event_type = "competitor_price_changed"
    else:
        return
    supabase.table("competitor_events").upsert({
        "competitor_id": competitor_id,
        "offering_id": offering_id,
        "event_type": event_type,
        "occurred_at": observed_at,
        "detail": {
            "offering_key": observation.offering_key,
            "display_name": observation.candidate.display_name,
            "qualifier": observation.candidate.qualifier,
            "old_price": existing.get("price_display") if existing else None,
            "new_price": observation.candidate.price_display,
            "source_url": observation.candidate.source_url,
        },
        "source_fingerprint": f"{event_type}:{observation.source_fingerprint}",
    }, on_conflict="source_fingerprint").execute()


def persist_candidate(
    supabase: Any, observation: ValidatedObservation, competitor_id: str,
    evidence_page_id: str, observed_at: str,
) -> None:
    """Keep one review candidate per source-backed fingerprint."""
    payload = offering_values(observation, competitor_id, evidence_page_id, observed_at)
    existing_rows = (supabase.table("competitor_offering_candidates").select("id")
                     .eq("source_fingerprint", observation.source_fingerprint).limit(1).execute().data)
    if existing_rows:
        supabase.table("competitor_offering_candidates").update({
            "last_seen_at": observed_at, "evidence_page_id": evidence_page_id,
        }).eq("id", existing_rows[0]["id"]).execute()
        return
    supabase.table("competitor_offering_candidates").insert({
        **payload,
        "review_status": "pending",
        "review_reason": observation.review_reason,
        "first_seen_at": observed_at,
    }).execute()


def crawl_competitor(supabase: Any, competitor: dict[str, Any], *, force: bool = False,
                     mock_documents: dict[str, list[PageDocument]] | None = None) -> str | None:
    """Crawl one website, retaining only evidence-bearing pages and current facts."""
    name = competitor.get("name", competitor["id"])
    if not competitor.get("website"):
        print(f"  [{name}] pricing crawl skipped: no website.", flush=True)
        return None
    if not force and crawl_is_fresh(supabase, competitor["id"]):
        print(f"  [{name}] pricing crawl skipped: completed within {CRAWL_FRESH_DAYS} days.", flush=True)
        return None
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    run = supabase.table("competitor_crawl_runs").insert({
        "competitor_id": competitor["id"], "status": "processing", "started_at": started_at,
    }).execute().data[0]
    print(f"  [{name}] pricing crawl started (run {run['id'][:8]}).", flush=True)
    documents: list[PageDocument] = []
    errors: list[dict[str, str]] = []
    try:
        if mock_documents is not None:
            documents = mock_documents.get(competitor["place_id"], [])
            selected_count = len(documents)
            print(f"  [{name}] using {selected_count} mock pricing page(s).", flush=True)
        else:
            print(f"  [{name}] fetching homepage.", flush=True)
            home = fetch_page(competitor["website"])
            sitemap = None
            try:
                sitemap_response = requests.get(urljoin(home.final_url, "/sitemap.xml"), headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
                if sitemap_response.ok:
                    sitemap = sitemap_response.text
            except requests.RequestException:
                pass
            urls = discover_urls(home, sitemap)
            selected_count = len(urls)
            print(f"  [{name}] selected {selected_count} pricing-relevant page(s).", flush=True)
            documents = [home]
            for page_number, url in enumerate(urls[1:], start=2):
                print(f"  [{name}] fetching page {page_number}/{selected_count}: {url}", flush=True)
                try:
                    document = fetch_page(url)
                    if _needs_render(document) and _is_high_signal(url):
                        print(f"  [{name}] rendering sparse page {page_number}/{selected_count}.", flush=True)
                        document = render_page(url)
                    documents.append(document)
                except Exception as exc:
                    print(f"  [{name}] page {page_number}/{selected_count} skipped: {exc}", flush=True)
                    errors.append({"url": url, "code": type(exc).__name__, "message": str(exc)})
    except Exception as exc:
        selected_count = 1
        print(f"  [{name}] homepage failed: {exc}", flush=True)
        errors.append({"url": competitor["website"], "code": type(exc).__name__, "message": str(exc)})

    aliases = {row["raw_name_key"]: row["service_catalog_id"] for row in supabase.table("competitor_service_aliases").select("raw_name_key, service_catalog_id").execute().data}
    catalog = {normalize_text(row["name"]): row["id"] for row in supabase.table("service_catalog").select("id, name").execute().data}
    seen_fingerprints: set[str] = set()
    for page_number, document in enumerate(documents, start=1):
        booking_host = registrable_host(document.final_url) in _booking_hosts()
        branded = not booking_host or normalize_text(competitor["name"]) in normalize_text(f"{document.title or ''} {document.visible_text}")
        candidates = extract_candidates(document)
        observations = unique_observations((
            validate_candidate(candidate, competitor["id"], competitor.get("market"), aliases, catalog, branded)
            for candidate in candidates
        ), seen_fingerprints)
        duplicates = len(candidates) - len(observations)
        if not observations:
            print(f"  [{name}] page {page_number}/{len(documents)} yielded no persisted facts.", flush=True)
            continue

        archive_path = f"{competitor['id']}/{run['id']}/{document.content_sha256}.html"
        _archive(supabase, archive_path, document.html)
        page = supabase.table("competitor_evidence_pages").insert({
            "crawl_run_id": run["id"], "competitor_id": competitor["id"],
            "source_url": document.url, "final_url": document.final_url,
            "render_mode": document.render_mode, "title": document.title,
            "http_status": document.http_status, "content_sha256": document.content_sha256,
            "archive_path": archive_path, "captured_at": started_at,
        }).execute().data[0]
        print(f"  [{name}] archived evidence page {page_number}/{len(documents)}; extracted {len(candidates)} candidate(s){f'; collapsed {duplicates} duplicate(s)' if duplicates else ''}.", flush=True)
        for observation in observations:
            try:
                if observation.publication_status == "published":
                    persist_published_offering(supabase, observation, competitor["id"], page["id"], started_at)
                else:
                    persist_candidate(supabase, observation, competitor["id"], page["id"], started_at)
            except Exception as exc:
                errors.append({"url": document.final_url, "code": type(exc).__name__, "message": f"{observation.candidate.display_name}: {exc}"})

    status = "complete" if documents and not errors else "partial" if documents else "failed"
    supabase.table("competitor_crawl_runs").update({
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": status,
        "pages_attempted": selected_count,
        "pages_succeeded": len(documents),
        "errors": errors,
    }).eq("id", run["id"]).execute()
    print(f"  [{name}] pricing crawl {status}: {len(documents)}/{selected_count} page(s) succeeded, {len(errors)} issue(s).", flush=True)
    return status
