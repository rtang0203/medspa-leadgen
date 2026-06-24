import datetime
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, Tuple
import urllib.parse
from .. import config
from .. import db

# Headers to mimic a real browser
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def detect_platform(html: str, headers: Dict[str, str], url: str) -> str:
    """Detect website builder platform from HTML source and headers."""
    html_lower = html.lower()
    url_lower = url.lower()
    
    # 1. Check URL first for obvious platforms
    if "linktr.ee" in url_lower:
        return "linktree"
    if "facebook.com" in url_lower:
        return "facebook"
        
    # 2. Wix fingerprints
    if "wix.com" in html_lower or "wixpress" in html_lower or "wix-code" in html_lower or "_wix" in html_lower:
        return "wix"
        
    # 3. Squarespace fingerprints
    if "squarespace.com" in html_lower or "static1.squarespace.com" in html_lower or "squarespace-headers" in html_lower:
        return "squarespace"
        
    # 4. WordPress fingerprints
    if "wp-content" in html_lower or "wp-includes" in html_lower or "wp-json" in html_lower:
        return "wordpress"
        
    # 5. Shopify fingerprints
    if "shopify.com" in html_lower or "cdn.shopify.com" in html_lower:
        return "shopify"
        
    return "custom"

def is_mobile_friendly_proxy(soup: BeautifulSoup) -> int:
    """Check if the site is likely mobile friendly by scanning for viewport meta."""
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if viewport:
        content = viewport.get("content", "").lower()
        if "width=device-width" in content or "initial-scale" in content:
            return 1
    return 0

def enrich_site_details(biz: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze the website and determine platform, SSL, and mobile friendliness."""
    place_id = biz["place_id"]
    url = biz.get("website_url")
    name = biz.get("name")
    
    now_iso = datetime.datetime.now().isoformat()
    
    # Defaults
    updates = {
        "site_fetched_at": now_iso,
        "site_fetch_status": "ok",
        "site_platform": "unknown",
        "has_ssl": None,
        "is_mobile_friendly": None
    }
    
    # Handle missing URL
    if not url:
        updates.update({
            "site_fetch_status": "no_site",
            "site_platform": "none",
            "has_ssl": 0,
            "is_mobile_friendly": 0
        })
        db.log_event(place_id, "enrich_site", "info", f"No website for {name}")
        return updates
        
    url_lower = url.lower()
    
    # Handle Facebook or Linktree URL directly
    if "facebook.com" in url_lower:
        updates.update({
            "site_fetch_status": "no_site",
            "site_platform": "facebook",
            "has_ssl": 1,
            "is_mobile_friendly": 1
        })
        db.log_event(place_id, "enrich_site", "info", f"Facebook-only page detected for {name}")
        return updates
        
    if "linktr.ee" in url_lower:
        updates.update({
            "site_fetch_status": "no_site",
            "site_platform": "linktree",
            "has_ssl": 1,
            "is_mobile_friendly": 1
        })
        db.log_event(place_id, "enrich_site", "info", f"Linktree page detected for {name}")
        return updates
        
    # Mock Mode handling
    if config.MOCK_MODE:
        db.log_event(place_id, "enrich_site", "info", f"[Mock] Fetching site for {name}: {url}")
        if "wix-example-medspa.com" in url_lower:
            updates.update({"site_platform": "wix", "has_ssl": 1, "is_mobile_friendly": 1})
        elif "squarespace-example-medspa.com" in url_lower:
            updates.update({"site_platform": "squarespace", "has_ssl": 1, "is_mobile_friendly": 1})
        elif "wordpress-outdated-medspa.com" in url_lower:
            updates.update({"site_platform": "wordpress", "has_ssl": 0, "is_mobile_friendly": 0})
        elif "custom-example-medspa.com" in url_lower:
            updates.update({"site_platform": "custom", "has_ssl": 1, "is_mobile_friendly": 1})
        else:
            updates.update({"site_platform": "custom", "has_ssl": 1, "is_mobile_friendly": 1})
        return updates
        
    # Real Mode fetching
    db.log_event(place_id, "enrich_site", "info", f"Fetching homepage for {name}: {url}")
    
    # Try HTTPS if it starts with HTTP, but let's respect the URL first
    # We will try to fetch the URL as is. If it fails with SSL, we record has_ssl = 0
    # Also, we check if the final redirected URL is https
    headers = {"User-Agent": USER_AGENT}
    
    try:
        # Check SSL by checking if https connection succeeds
        # We perform the request. If it raises requests.exceptions.SSLError, has_ssl = 0
        has_ssl = 1
        if url.startswith("http://"):
            # Try to upgrade to https to see if they support SSL
            https_url = url.replace("http://", "https://", 1)
            try:
                test_resp = requests.head(https_url, headers=headers, timeout=5, allow_redirects=True)
                if test_resp.status_code < 400:
                    url = https_url
            except requests.exceptions.SSLError:
                has_ssl = 0
            except Exception:
                has_ssl = 0
                
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        response.raise_for_status()
        
        # Check if the final URL is HTTPS
        final_url = response.url
        if not final_url.startswith("https://"):
            has_ssl = 0
            
        soup = BeautifulSoup(response.text, "html.parser")
        
        platform = detect_platform(response.text, response.headers, final_url)
        mobile = is_mobile_friendly_proxy(soup)
        
        updates.update({
            "site_fetch_status": "ok",
            "site_platform": platform,
            "has_ssl": has_ssl,
            "is_mobile_friendly": mobile
        })
        db.log_event(place_id, "enrich_site", "info", f"Successfully analyzed site for {name}. Platform: {platform}, SSL: {has_ssl}, Mobile: {mobile}")
        
    except requests.exceptions.Timeout:
        updates.update({
            "site_fetch_status": "timeout",
            "site_platform": "unknown"
        })
        db.log_event(place_id, "enrich_site", "warn", f"Timeout fetching {url} for {name}")
    except requests.exceptions.SSLError:
        updates.update({
            "site_fetch_status": "error",
            "site_platform": "unknown",
            "has_ssl": 0
        })
        db.log_event(place_id, "enrich_site", "warn", f"SSL Error fetching {url} for {name}")
    except Exception as e:
        updates.update({
            "site_fetch_status": "error",
            "site_platform": "unknown"
        })
        db.log_event(place_id, "enrich_site", "error", f"Error fetching {url} for {name}: {str(e)}")
        
    return updates

def run_enrich_site():
    """Run website enrichment for all businesses in the DB that haven't been checked."""
    db.log_event(None, "enrich_site", "info", "Starting website enrichment stage")
    businesses = db.get_businesses_to_enrich("enrich_site")
    print(f"Found {len(businesses)} businesses needing website enrichment.")
    
    enriched_count = 0
    for biz in businesses:
        updates = enrich_site_details(biz)
        db.update_business(biz["place_id"], updates)
        enriched_count += 1
        
    db.log_event(None, "enrich_site", "info", f"Completed website enrichment stage for {enriched_count} businesses")
    print(f"Enriched websites for {enriched_count} businesses.")
