import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from medspa_leads.competitor_pricing import (
    MAX_BODY_BYTES,
    OfferingCandidate,
    PageDocument,
    _needs_render,
    canonical_url,
    discover_urls,
    extract_candidates,
    fetch_page,
    normalize_text,
    unique_observations,
    validate_candidate,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pricing"


def fixture_document(filename: str, *, url: str = "https://example.com/pricing") -> PageDocument:
    html = (FIXTURES / filename).read_text()
    return PageDocument(url, url, "Example", html, " ".join(extract_candidates.__module__ and []), hashlib.sha256(html.encode()).hexdigest(), 200, "http")


def candidates(filename: str):
    return extract_candidates(fixture_document(filename))


def test_static_menu_extracts_fixed_from_range_and_units():
    observed = candidates("static-menu.html")
    assert [(item.display_name, item.price_type, item.amount_min, item.amount_max, item.price_unit) for item in observed] == [
        ("Botox/Dysport", "fixed", Decimal("12"), None, "unit"),
        ("Microneedling", "from", Decimal("299"), None, "session"),
        ("IPL Photofacial", "range", Decimal("250"), Decimal("450"), "session"),
    ]


def test_membership_and_multi_session_package_are_exact():
    observed = candidates("membership-package.html")
    assert [(item.offering_type, item.price_unit, item.package_quantity) for item in observed] == [
        ("membership", "month", None),
        ("package", "package", 3),
    ]


def test_offer_dates_and_crossed_out_current_price_are_preserved():
    observed = candidates("offer.html")
    assert observed[0].valid_through == date(2026, 8, 31)
    assert observed[1].amount_min == Decimal("299")
    assert observed[1].original_amount == Decimal("399")
    assert observed[1].price_display == "now $299"


def test_json_ld_offer_is_extracted():
    html = '''<script type="application/ld+json">{"@type":"Product","name":"Chemical Peel","offers":{"@type":"Offer","price":"199","priceCurrency":"USD"}}</script>'''
    doc = PageDocument("https://example.com", "https://example.com", None, html, "", "x", 200, "http")
    found = extract_candidates(doc)
    assert [(item.display_name, item.amount_min, item.currency) for item in found] == [("Chemical Peel", Decimal("199"), "USD")]




def test_duplicate_observations_share_a_single_crawl_fingerprint():
    first = validate_candidate(candidate(), "competitor", "Chicago, IL")
    duplicate = validate_candidate(candidate(), "competitor", "Chicago, IL")
    distinct = validate_candidate(candidate(display_name="Xeomin", evidence_text="Xeomin - $12 per unit"), "competitor", "Chicago, IL")
    seen: set[str] = set()

    assert unique_observations([first, duplicate, distinct], seen) == [first, distinct]
    assert unique_observations([first], seen) == []
def candidate(**changes):
    base = dict(offering_type="service", display_name="Botox/Dysport", qualifier=None, price_type="fixed", amount_min=Decimal("12"), amount_max=None, currency="USD", price_unit="unit", package_quantity=None, original_amount=None, valid_from=None, valid_through=None, price_display="$12", evidence_text="Botox/Dysport - $12 per unit", source_url="https://example.com/pricing")
    base.update(changes)
    return OfferingCandidate(**base)


def test_validated_price_publishes_only_with_source_evidence():
    validated = validate_candidate(candidate(), "competitor", "Chicago, IL")
    assert validated.publication_status == "published"
    assert validated.review_reason is None


@pytest.mark.parametrize(("changes", "reason"), [
    ({"price_display": "$13"}, "amount_not_in_evidence"),
    ({"display_name": "Xeomin"}, "name_not_in_evidence"),
    ({"currency": None}, "unknown_currency"),
    ({"price_type": "range", "amount_max": None}, "ambiguous_price_semantics"),
])
def test_invalid_evidence_stays_pending_with_exact_reason(changes, reason):
    validated = validate_candidate(candidate(**changes), "competitor", None)
    assert (validated.publication_status, validated.review_reason) == ("pending", reason)


def test_priceless_service_requires_catalog_mapping():
    availability = candidate(price_type="none", amount_min=None, currency=None, price_unit=None, price_display=None, evidence_text="Botox/Dysport available")
    pending = validate_candidate(availability, "competitor", "Chicago, IL")
    published = validate_candidate(availability, "competitor", "Chicago, IL", {normalize_text("Botox/Dysport"): "catalog"})
    assert pending.review_reason == "service_mapping_needed"
    assert (published.publication_status, published.service_catalog_id) == ("published", "catalog")


def test_unbranded_booking_candidate_remains_pending():
    validated = validate_candidate(candidate(source_url="https://booksy.com/example"), "competitor", "Chicago, IL", booking_branded=False)
    assert validated.review_reason == "unverified_booking_brand"


def test_url_discovery_caps_depth_one_and_strips_tracking():
    html = "".join(f'<a href="/services/{i}?utm_source=x">Pricing {i}</a>' for i in range(20))
    home = PageDocument("https://example.com", "https://example.com", None, html, "", "x", 200, "http")
    urls = discover_urls(home)
    assert len(urls) == 12
    assert all("utm_" not in url for url in urls)
    assert canonical_url("https://x.test/a#b?ignored") == "https://x.test/a"


def test_sparse_high_signal_page_selects_browser_fallback():
    sparse = PageDocument("https://example.com/pricing", "https://example.com/pricing", None, '<div id="root"></div>', "", "x", 200, "http")
    assert _needs_render(sparse)


class FakeResponse:
    def __init__(self, status=200, content_type="text/html", chunks=(b"<html>ok</html>",)):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"
        self.url = "https://example.com/final"
        self._chunks = chunks
        self.ok = status < 400
        self.text = "User-agent: *\nAllow: /"

    def iter_content(self, chunk_size):
        return iter(self._chunks)


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, url, **kwargs):
        return FakeResponse() if url.endswith("robots.txt") else self.response


@pytest.mark.parametrize(("response", "error"), [
    (FakeResponse(content_type="application/pdf"), "non_html"),
    (FakeResponse(chunks=(b"x" * (MAX_BODY_BYTES + 1),)), "oversized_body"),
    (FakeResponse(status=503), "http_503"),
])
def test_fetch_rejects_non_html_oversized_and_http_errors(response, error):
    with pytest.raises(RuntimeError, match=error):
        fetch_page("https://example.com/pricing", FakeSession(response))


def test_fetch_honors_robots_denial(monkeypatch):
    monkeypatch.setattr("medspa_leads.competitor_pricing._request_allowed", lambda url, session: False)
    with pytest.raises(RuntimeError, match="robots_denied"):
        fetch_page("https://example.com/pricing", FakeSession(FakeResponse()))
