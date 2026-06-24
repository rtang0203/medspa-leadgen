import datetime
from typing import Dict, Any, Tuple, Optional
from .. import config
from .. import db

def calculate_score(biz: Dict[str, Any]) -> Tuple[int, Optional[str]]:
    """
    Calculate the deficiency score and determine the primary deficiency
    for a business based on its current enrichment state.
    
    Returns:
        (score, primary_deficiency)
    """
    score = 0
    
    # 1. Determine website status (no_website)
    # Can be triggered by site_platform or simply a missing website_url from Places.
    site_platform = biz.get("site_platform")
    website_url = biz.get("website_url")
    site_fetched_at = biz.get("site_fetched_at")
    
    is_no_website = False
    if site_platform in ("none", "linktree", "facebook"):
        is_no_website = True
    elif not site_platform:
        # If website analysis hasn't run yet, check the raw URL from Places
        if not website_url:
            is_no_website = True
        else:
            url_lower = website_url.lower()
            if "linktr.ee" in url_lower or "facebook.com" in url_lower:
                is_no_website = True
                
    if is_no_website:
        score += config.WEIGHTS.get("no_website", 3)
        
    # 2. Online Booking
    has_online_booking = biz.get("has_online_booking")
    is_no_booking = has_online_booking == 0
    if is_no_booking:
        score += config.WEIGHTS.get("no_booking", 2)
        
    # 3. Mobile Friendly
    is_mobile_friendly = biz.get("is_mobile_friendly")
    is_not_mobile = is_mobile_friendly == 0
    if is_not_mobile:
        score += config.WEIGHTS.get("not_mobile", 2)
        
    # 4. SSL
    has_ssl = biz.get("has_ssl")
    is_no_ssl = has_ssl == 0
    if is_no_ssl:
        score += config.WEIGHTS.get("no_ssl", 1)
        
    # 5. Social Signal
    social_status = biz.get("social_status")
    is_dormant_social = social_status == "dormant"
    is_no_social = social_status == "none"
    
    if is_dormant_social:
        score += config.WEIGHTS.get("dormant_social", 1)
    elif is_no_social:
        score += config.WEIGHTS.get("no_social", 1)
        
    # 6. Bonus Signal: reviews_vs_web
    # +2 if review_count >= 100 AND (no_website OR not_mobile OR no_booking)
    review_count = biz.get("review_count") or 0
    reviews_vs_web_applicable = False
    if review_count >= 100 and (is_no_website or is_not_mobile or is_no_booking):
        score += 2
        reviews_vs_web_applicable = True
        
    # 7. Select Primary Deficiency
    # Override logic: reviews_vs_web overrides everything else if applicable
    primary_deficiency = None
    if reviews_vs_web_applicable:
        primary_deficiency = "reviews_vs_web"
    elif is_no_website:
        primary_deficiency = "no_website"
    elif is_no_booking:
        primary_deficiency = "no_booking"
    elif is_not_mobile:
        primary_deficiency = "not_mobile"
    elif is_no_ssl:
        primary_deficiency = "no_ssl"
    elif is_dormant_social:
        primary_deficiency = "dormant_social"
    elif is_no_social:
        primary_deficiency = "no_social"
        
    return score, primary_deficiency

def score_businesses():
    """Score all businesses in the database and save the results."""
    db.log_event(None, "score", "info", "Starting scoring process for all businesses")
    businesses = db.get_all_businesses()
    scored_count = 0
    
    for biz in businesses:
        place_id = biz["place_id"]
        score, primary_def = calculate_score(biz)
        
        updates = {
            "deficiency_score": score,
            "primary_deficiency": primary_def,
            "scored_at": datetime.datetime.now().isoformat()
        }
        db.update_business(place_id, updates)
        scored_count += 1
        
    db.log_event(None, "score", "info", f"Completed scoring for {scored_count} businesses")
    print(f"Scored {scored_count} businesses.")
