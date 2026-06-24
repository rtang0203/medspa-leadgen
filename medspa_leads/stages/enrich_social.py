import datetime
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, Tuple
from .. import config
from .. import db

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def find_social_links(html: str, soup: BeautifulSoup) -> Tuple[str, str]:
    """Scan HTML and soup for Instagram and Facebook links."""
    instagram_url = None
    facebook_url = None
    
    for link in soup.find_all("a", href=True):
        href = link["href"]
        href_lower = href.lower()
        
        if "instagram.com" in href_lower and not instagram_url:
            instagram_url = href
        elif ("facebook.com" in href_lower or "facebook.co" in href_lower) and not facebook_url:
            # Avoid sharing links like sharing buttons
            if "sharer" not in href_lower:
                facebook_url = href
                
    return instagram_url, facebook_url

def enrich_social_details(biz: Dict[str, Any]) -> Dict[str, Any]:
    """Check website and Places data for social links and status."""
    place_id = biz["place_id"]
    url = biz.get("website_url")
    name = biz.get("name")
    site_platform = biz.get("site_platform")
    
    now_iso = datetime.datetime.now().isoformat()
    
    updates = {
        "social_checked_at": now_iso,
        "instagram_url": None,
        "facebook_url": None,
        "social_last_post": None,
        "social_status": "none"
    }
    
    # Check if Places URL itself is facebook
    if url and "facebook.com" in url.lower():
        updates.update({
            "facebook_url": url,
            "social_status": "unknown"
        })
        # Mock override for facebook-only mock lead
        if config.MOCK_MODE:
            updates.update({
                "social_status": "active",
                "social_last_post": (datetime.datetime.now() - datetime.timedelta(days=3)).date().isoformat()
            })
        return updates
        
    if url and "linktr.ee" in url.lower():
        # In mock mode, we assume linktree leads have a dormant social linked
        if config.MOCK_MODE:
            updates.update({
                "instagram_url": "https://instagram.com/zendayspa",
                "social_status": "dormant",
                "social_last_post": (datetime.datetime.now() - datetime.timedelta(days=180)).date().isoformat()
            })
        return updates
        
    if not url:
        return updates
        
    url_lower = url.lower()
    
    # Mock Mode handling
    if config.MOCK_MODE:
        db.log_event(place_id, "enrich_social", "info", f"[Mock] Checking social for {name}: {url}")
        if "wix-example-medspa.com" in url_lower:
            updates.update({
                "instagram_url": "https://instagram.com/eliteskin",
                "facebook_url": "https://facebook.com/eliteskin",
                "social_status": "active",
                "social_last_post": (datetime.datetime.now() - datetime.timedelta(days=1)).date().isoformat()
            })
        elif "squarespace-example-medspa.com" in url_lower:
            updates.update({
                "instagram_url": "https://instagram.com/vanishmedspa",
                "social_status": "active",
                "social_last_post": (datetime.datetime.now() - datetime.timedelta(days=2)).date().isoformat()
            })
        elif "wordpress-outdated-medspa.com" in url_lower:
            updates.update({
                "instagram_url": "https://instagram.com/classicderm",
                "facebook_url": "https://facebook.com/classicderm",
                "social_status": "dormant",
                "social_last_post": (datetime.datetime.now() - datetime.timedelta(days=200)).date().isoformat()
            })
        elif "custom-example-medspa.com" in url_lower:
            updates.update({
                "instagram_url": "https://instagram.com/revitalizemedspa",
                "social_status": "active",
                "social_last_post": (datetime.datetime.now() - datetime.timedelta(days=4)).date().isoformat()
            })
        return updates
        
    # Real Mode fetching website
    db.log_event(place_id, "enrich_social", "info", f"Fetching website to check social links for {name}")
    try:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        ig, fb = find_social_links(response.text, soup)
        
        updates["instagram_url"] = ig
        updates["facebook_url"] = fb
        
        if ig or fb:
            updates["social_status"] = "unknown" # Default since we don't scrape profiles
        else:
            updates["social_status"] = "none"
            
        db.log_event(place_id, "enrich_social", "info", f"Finished social check for {name}. IG: {ig}, FB: {fb}")
        
    except Exception as e:
        db.log_event(place_id, "enrich_social", "error", f"Error checking social for {url}: {str(e)}")
        
    return updates

def run_enrich_social():
    """Run social enrichment for all businesses in the DB that haven't been checked."""
    db.log_event(None, "enrich_social", "info", "Starting social enrichment stage")
    businesses = db.get_businesses_to_enrich("enrich_social")
    print(f"Found {len(businesses)} businesses needing social enrichment.")
    
    enriched_count = 0
    for biz in businesses:
        updates = enrich_social_details(biz)
        db.update_business(biz["place_id"], updates)
        enriched_count += 1
        
    db.log_event(None, "enrich_social", "info", f"Completed social enrichment for {enriched_count} businesses")
    print(f"Enriched social info for {enriched_count} businesses.")
