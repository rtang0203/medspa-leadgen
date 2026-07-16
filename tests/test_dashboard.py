import datetime
from types import SimpleNamespace

import pytest

from medspa_leads.dashboard import (
    MARKET_CACHE_DAYS,
    market_is_fresh,
    explicit_markets,
    normalize_metro,
    require_local_mock_target,
    primary_market_tenants,
)
from medspa_leads.competitor_pricing import CRAWL_FRESH_DAYS, crawl_is_fresh


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _columns):
        return self

    def eq(self, _column, _value):
        return self

    def in_(self, _column, _values):
        return self

    def order(self, _column, desc=False):
        return self

    @property
    def not_(self):
        return self

    def is_(self, _column, _value):
        return self

    def limit(self, _count):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeSupabase:
    def __init__(self, tables):
        self.tables = tables
        self.table_calls = []

    def table(self, name):
        self.table_calls.append(name)
        return FakeQuery(self.tables[name])


def test_explicit_markets_normalize_and_deduplicate_without_tenant_links():
    assert explicit_markets([" Chicago,   IL ", "Milwaukee, WI", "chicago, il"]) == {
        "chicago, il": {"metro": "Chicago, IL", "tenant_ids": []},
        "milwaukee, wi": {"metro": "Milwaukee, WI", "tenant_ids": []},
    }


@pytest.mark.parametrize("metros", [[], ["   "]])
def test_explicit_markets_rejects_empty_or_blank_values(metros):
    with pytest.raises(ValueError, match="metro"):
        explicit_markets(metros)


def test_mock_dashboard_writes_require_local_supabase(monkeypatch):
    monkeypatch.setattr("medspa_leads.dashboard.config.SUPABASE_URL", "https://project.supabase.co")
    with pytest.raises(ValueError, match="local Supabase"):
        require_local_mock_target()

    monkeypatch.setattr("medspa_leads.dashboard.config.SUPABASE_URL", "http://127.0.0.1:54321")
    require_local_mock_target()


def test_normalize_metro_collapses_whitespace_and_casefolds_key():
    assert normalize_metro("  Chicago,   IL  ") == ("Chicago, IL", "chicago, il")


@pytest.mark.parametrize("metro", ["", "   ", "\t\n"])
def test_normalize_metro_rejects_blank_values(metro):
    with pytest.raises(ValueError, match="metro is blank"):
        normalize_metro(metro)


def test_primary_market_tenants_deduplicates_shared_metros():
    supabase = FakeSupabase(
        {
            "locations": [
                {"tenant_id": "tenant-a", "metro": "Chicago, IL"},
                {"tenant_id": "tenant-b", "metro": " Chicago,  IL "},
                {"tenant_id": "tenant-c", "metro": "Milwaukee, WI"},
            ]
        }
    )

    markets = primary_market_tenants(supabase)

    assert markets == {
        "chicago, il": {
            "metro": "Chicago, IL",
            "tenant_ids": ["tenant-a", "tenant-b"],
        },
        "milwaukee, wi": {
            "metro": "Milwaukee, WI",
            "tenant_ids": ["tenant-c"],
        },
    }
    assert supabase.table_calls == ["locations"]


def test_primary_market_tenants_rejects_blank_primary_metro():
    supabase = FakeSupabase({"locations": [{"tenant_id": "tenant-a", "metro": "  "}]})

    with pytest.raises(ValueError, match="Primary locations with blank metros: tenant-a"):
        primary_market_tenants(supabase)


def test_primary_market_tenants_requires_at_least_one_market():
    with pytest.raises(ValueError, match="No primary-location metros"):
        primary_market_tenants(FakeSupabase({"locations": []}))


def test_market_freshness_uses_completed_market_scrape_time():
    completed_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=MARKET_CACHE_DAYS - 1
    )
    supabase = FakeSupabase(
        {"competitor_market_scrapes": [{"last_completed_at": completed_at.isoformat(), "last_status": "complete"}]}
    )

    assert market_is_fresh(supabase, "chicago, il")
    assert supabase.table_calls == ["competitor_market_scrapes"]


def test_market_freshness_expires_after_cache_window():
    completed_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=MARKET_CACHE_DAYS
    )
    supabase = FakeSupabase(
        {"competitor_market_scrapes": [{"last_completed_at": completed_at.isoformat(), "last_status": "complete"}]}
    )

    assert not market_is_fresh(supabase, "chicago, il")


def test_website_refresh_is_independent_of_market_discovery_cache():
    completed_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=CRAWL_FRESH_DAYS - 1
    )
    fresh = FakeSupabase({"competitor_crawl_runs": [{"completed_at": completed_at.isoformat()}]})
    stale = FakeSupabase({"competitor_crawl_runs": [{"completed_at": (completed_at - datetime.timedelta(days=CRAWL_FRESH_DAYS)).isoformat()}]})

    assert crawl_is_fresh(fresh, "competitor-a")
    assert not crawl_is_fresh(stale, "competitor-a")


def test_missing_completed_crawl_is_due_for_website_refresh():
    assert not crawl_is_fresh(FakeSupabase({"competitor_crawl_runs": []}), "competitor-a")
