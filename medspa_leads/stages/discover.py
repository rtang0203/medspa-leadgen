import datetime
import urllib.parse
import requests
from typing import List, Dict, Any
from .. import config
from .. import db

# Cache limit: Do not re-discover the same metro within 7 days
METRO_CACHE_DAYS = 7

def is_metro_cached(metro: str) -> bool:
    """Check if the metro was queried recently to save Places API cost."""
    conn = db.get_db_connection()
    cursor = conn.cursor()
    
    # We look for a 'discover' stage log for this metro in the event log
    # Let's search for messages containing this metro in the last N days
    since = (datetime.datetime.now() - datetime.timedelta(days=METRO_CACHE_DAYS)).isoformat()
    
    cursor.execute(
        "SELECT id FROM events WHERE stage = 'discover' AND level = 'info' AND message LIKE ? AND created_at > ?",
        (f"%Completed discovery for metro: {metro}%", since)
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None

def get_mock_businesses(metro: str) -> List[Dict[str, Any]]:
    """Generate realistic mock businesses for testing/offline mode."""
    # We want a variety of mock businesses to test scoring and enrichment stages:
    # 1. No website (points to facebook)
    # 2. No website (points to linktree)
    # 3. No website (empty website)
    # 4. Wix website, no online booking, mobile friendly, SSL
    # 5. Squarespace website, online booking (Vagaro), mobile friendly, SSL
    # 6. Wordpress website, no booking, not mobile friendly, no SSL, high reviews (reviews_vs_web candidate)
    # 7. Custom website, online booking (Mindbody), mobile friendly, SSL
    
    now_iso = datetime.datetime.now().isoformat()
    
    mock_templates = [
        {
            "place_id": f"mock_biz_{metro.lower().replace(' ', '_').replace(',', '')}_1",
            "name": "Radiant Glow Med Spa",
            "metro": metro,
            "address": f"101 Beauty Blvd, {metro}",
            "phone": "+1-512-555-0101",
            "website_url": "https://facebook.com/radiantglowmedspa",
            "rating": 4.2,
            "review_count": 45,
            "discovered_at": now_iso
        },
        {
            "place_id": f"mock_biz_{metro.lower().replace(' ', '_').replace(',', '')}_2",
            "name": "Zen Day & Medical Spa",
            "metro": metro,
            "address": f"202 Harmony Way, {metro}",
            "phone": "+1-512-555-0202",
            "website_url": "https://linktr.ee/zendayspa",
            "rating": 4.7,
            "review_count": 89,
            "discovered_at": now_iso
        },
        {
            "place_id": f"mock_biz_{metro.lower().replace(' ', '_').replace(',', '')}_3",
            "name": "Nirvana Wellness",
            "metro": metro,
            "address": f"303 Nirvana Cir, {metro}",
            "phone": None,
            "website_url": None,
            "rating": 3.9,
            "review_count": 12,
            "discovered_at": now_iso
        },
        {
            "place_id": f"mock_biz_{metro.lower().replace(' ', '_').replace(',', '')}_4",
            "name": "Elite Skin & Laser",
            "metro": metro,
            "address": f"404 Laser Ln, {metro}",
            "phone": "+1-512-555-0404",
            "website_url": "https://wix-example-medspa.com", # In mock mode, we will stub fetch responses
            "rating": 4.5,
            "review_count": 75,
            "discovered_at": now_iso
        },
        {
            "place_id": f"mock_biz_{metro.lower().replace(' ', '_').replace(',', '')}_5",
            "name": "Vanish Medical Spa",
            "metro": metro,
            "address": f"505 Beauty Way, {metro}",
            "phone": "+1-512-555-0505",
            "website_url": "https://squarespace-example-medspa.com",
            "rating": 4.9,
            "review_count": 150,
            "discovered_at": now_iso
        },
        {
            "place_id": f"mock_biz_{metro.lower().replace(' ', '_').replace(',', '')}_6",
            "name": "Classic Derm & Spa",
            "metro": metro,
            "address": f"606 Vintage Rd, {metro}",
            "phone": "+1-512-555-0606",
            "website_url": "http://wordpress-outdated-medspa.com", # No SSL, http
            "rating": 4.8,
            "review_count": 312, # High reviews + outdated web presence = reviews_vs_web!
            "discovered_at": now_iso
        },
        {
            "place_id": f"mock_biz_{metro.lower().replace(' ', '_').replace(',', '')}_7",
            "name": "Revitalize MedSpa",
            "metro": metro,
            "address": f"707 Wellness Dr, {metro}",
            "phone": "+1-512-555-0707",
            "website_url": "https://custom-example-medspa.com",
            "rating": 4.6,
            "review_count": 60,
            "discovered_at": now_iso
        }
    ]
    return mock_templates

def fetch_place_details(place_id: str) -> Dict[str, Any]:
    """Fetch phone number and website for a place using Place Details API."""
    db.log_event(place_id, "discover", "info", f"Fetching place details for {place_id}")
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "formatted_phone_number,website",
        "key": config.GOOGLE_PLACES_API_KEY
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") == "OK":
            result = data.get("result", {})
            return {
                "phone": result.get("formatted_phone_number"),
                "website_url": result.get("website")
            }
        else:
            db.log_event(place_id, "discover", "error", f"Details API error status: {data.get('status')}")
    except Exception as e:
        db.log_event(place_id, "discover", "error", f"Details API exception: {str(e)}")
        
    return {"phone": None, "website_url": None}

def discover_metro(metro: str, force: bool = False) -> int:
    """Discover med spas in a metro and upsert them to the database."""
    if not force and is_metro_cached(metro):
        db.log_event(None, "discover", "info", f"Metro cache hit: {metro}. Skipping discovery.")
        print(f"Discovery for metro '{metro}' is cached within last {METRO_CACHE_DAYS} days. Skipping (use force=True to override).")
        return 0
        
    db.log_event(None, "discover", "info", f"Starting discovery for metro: {metro}")
    print(f"Starting discovery for metro: {metro}")
    
    if config.MOCK_MODE:
        db.log_event(None, "discover", "info", "Mock mode active. Generating mock businesses.")
        businesses = get_mock_businesses(metro)
        for biz in businesses:
            db.upsert_business(biz)
        db.log_event(None, "discover", "info", f"Completed discovery for metro: {metro} (Mock, upserted {len(businesses)} businesses)")
        return len(businesses)
        
    # Real mode
    if not config.GOOGLE_PLACES_API_KEY:
        db.log_event(None, "discover", "error", "Google Places API key is missing. Cannot perform real search.")
        raise ValueError("Google Places API key is missing. Set GOOGLE_PLACES_API_KEY in .env or config.")
        
    queries = ["med spa", "day spa", "medical spa"]
    discovered_count = 0
    conn = db.get_db_connection()
    cursor = conn.cursor()
    
    for base_query in queries:
        query_str = f"{base_query} in {metro}"
        db.log_event(None, "discover", "info", f"Running search for query: {query_str}")
        
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": query_str,
            "key": config.GOOGLE_PLACES_API_KEY
        }
        
        try:
            # We fetch first page only for PoC to control costs and time
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                db.log_event(None, "discover", "error", f"Search API error status: {data.get('status')}")
                continue
                
            results = data.get("results", [])
            for result in results:
                place_id = result["place_id"]
                name = result["name"]
                address = result.get("formatted_address")
                rating = result.get("rating")
                review_count = result.get("user_ratings_total")
                
                # Check if place is already in database
                cursor.execute("SELECT phone, website_url FROM businesses WHERE place_id = ?", (place_id,))
                existing = cursor.fetchone()
                
                phone = None
                website_url = None
                
                if existing:
                    # Keep existing phone and website if we have them
                    phone = existing["phone"]
                    website_url = existing["website_url"]
                    db.log_event(place_id, "discover", "info", f"Already in DB: {name}. Skipping details fetch.")
                else:
                    # New place! Fetch details (phone, website)
                    details = fetch_place_details(place_id)
                    phone = details.get("phone")
                    website_url = details.get("website_url")
                    
                biz_dict = {
                    "place_id": place_id,
                    "name": name,
                    "metro": metro,
                    "address": address,
                    "phone": phone,
                    "website_url": website_url,
                    "rating": rating,
                    "review_count": review_count,
                    "discovered_at": datetime.datetime.now().isoformat()
                }
                db.upsert_business(biz_dict)
                discovered_count += 1
                
        except Exception as e:
            db.log_event(None, "discover", "error", f"Search API exception for {query_str}: {str(e)}")
            
    conn.close()
    db.log_event(None, "discover", "info", f"Completed discovery for metro: {metro} (Real, upserted {discovered_count} businesses)")
    return discovered_count
