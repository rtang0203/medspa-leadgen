import datetime
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, Tuple, Optional
from .. import config
from .. import db

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Signatures to look for in HTML/hrefs/scripts
BOOKING_SIGNATURES = {
    "vagaro": ["vagaro.com"],
    "mindbody": ["mindbodyonline.com", "mindbody.io"],
    "boulevard": ["joinblvd.com", "blvd.com", "boulevard.com"],
    "square": ["square.site", "squareup.com/appointments"],
    "acuity": ["acuityscheduling.com"],
    "calendly": ["calendly.com"],
    "glossgenius": ["glossgenius.com"],
    "zenoti": ["zenoti.com"],
    "booksy": ["booksy.com"],
    "fresha": ["fresha.com"],
    "jane": ["janeapp.com"],
    "mangomint": ["mangomint.com"],
    "aesthetic_record": ["aestheticrecord.com"],
    "pabau": ["pabau.com"],
    "setmore": ["setmore.com"],
}

def detect_booking_platform(html: str, soup: BeautifulSoup) -> Tuple[int, Optional[str]]:
    """
    Scan HTML content and links for booking widgets/platforms.
    Returns (has_online_booking, booking_platform)
    """
    html_lower = html.lower()
    
    # 1. Search for known platforms in raw html and script tags
    for platform, signatures in BOOKING_SIGNATURES.items():
        for sig in signatures:
            if sig in html_lower:
                return 1, platform
                
    # 2. Search in all anchor tags
    for link in soup.find_all("a", href=True):
        href = link["href"].lower()
        # Check signatures
        for platform, signatures in BOOKING_SIGNATURES.items():
            for sig in signatures:
                if sig in href:
                    return 1, platform
                    
        # Check for "book" or "schedule" in link text or href
        link_text = link.get_text().lower()
        if any(w in href for w in ("book", "schedul", "appointment")) or any(w in link_text for w in ("book", "schedule")):
            return 1, "other"

    # 3. Check for booking buttons/CTAs — look for booking text inside clickable elements
    #    (buttons, inputs, spans with onclick). Avoids false positives from prose like
    #    "call us to book your appointment."
    for el in soup.find_all(["button", "input"]):
        el_text = (el.get("value", "") + " " + el.get_text()).lower()
        if any(w in el_text for w in ("book", "schedule")):
            return 1, "other"
        
    return 0, None

def enrich_booking_details(biz: Dict[str, Any]) -> Dict[str, Any]:
    """Check the website for booking links or scripts."""
    place_id = biz["place_id"]
    url = biz.get("website_url")
    name = biz.get("name")
    site_platform = biz.get("site_platform")
    
    now_iso = datetime.datetime.now().isoformat()
    
    updates = {
        "booking_checked_at": now_iso,
        "has_online_booking": 0,
        "booking_platform": None
    }
    
    # If no website or social-only, it's highly unlikely to have automated online booking
    if not url or site_platform in ("none", "facebook", "linktree"):
        db.log_event(place_id, "enrich_booking", "info", f"Skipping booking check for {name} (no website or social-only)")
        return updates
        
    url_lower = url.lower()
    
    # Mock Mode handling
    if config.MOCK_MODE:
        db.log_event(place_id, "enrich_booking", "info", f"[Mock] Checking booking for {name}: {url}")
        if "squarespace-example-medspa.com" in url_lower:
            updates.update({"has_online_booking": 1, "booking_platform": "vagaro"})
        elif "custom-example-medspa.com" in url_lower:
            updates.update({"has_online_booking": 1, "booking_platform": "mindbody"})
        else:
            # Wix and Wordpress mocks don't have booking
            updates.update({"has_online_booking": 0, "booking_platform": None})
        return updates
        
    # Real Mode fetching
    db.log_event(place_id, "enrich_booking", "info", f"Fetching {url} to check online booking for {name}")
    
    try:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        has_booking, platform = detect_booking_platform(response.text, soup)
        
        updates.update({
            "has_online_booking": has_booking,
            "booking_platform": platform
        })
        db.log_event(place_id, "enrich_booking", "info", f"Finished booking check for {name}. Has booking: {has_booking}, Platform: {platform}")
        
    except Exception as e:
        db.log_event(place_id, "enrich_booking", "error", f"Error checking booking for {url}: {str(e)}")
        # In case of error, we default to 0 booking and log it
        
    return updates

def run_enrich_booking():
    """Run online booking enrichment for all businesses that haven't been checked."""
    db.log_event(None, "enrich_booking", "info", "Starting booking enrichment stage")
    businesses = db.get_businesses_to_enrich("enrich_booking")
    print(f"Found {len(businesses)} businesses needing booking enrichment.")
    
    enriched_count = 0
    for biz in businesses:
        updates = enrich_booking_details(biz)
        db.update_business(biz["place_id"], updates)
        enriched_count += 1
        
    db.log_event(None, "enrich_booking", "info", f"Completed booking enrichment stage for {enriched_count} businesses")
    print(f"Enriched booking info for {enriched_count} businesses.")
