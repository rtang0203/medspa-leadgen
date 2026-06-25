import datetime
import json
import requests
from typing import Dict, Any, Optional
from .. import config
from .. import db

def generate_template_hook(biz: Dict[str, Any]) -> str:
    """Generate a high-quality template-based hook grounded in business facts."""
    name = biz.get("name", "your spa")
    review_count = biz.get("review_count") or 0
    primary_def = biz.get("primary_deficiency")
    site_platform = biz.get("site_platform")
    
    # Clean up name for the hook (make it lowercase-ok, remove trailing inc/corp)
    name_clean = name.lower()
    for suffix in [" inc", " corp", " llc", " med spa", " medical spa", " spa"]:
        if name_clean.endswith(suffix):
            name_clean = name_clean[:-len(suffix)].strip()
    if not name_clean:
        name_clean = name.lower()
        
    if primary_def == "reviews_vs_web":
        return f"noticed {name_clean} has {review_count}+ five-star reviews but no online booking on the site — you're probably losing after-hours bookings to whoever picks up the phone first."
        
    elif primary_def == "no_website":
        if site_platform == "facebook":
            return f"noticed {name_clean} has a strong social presence but relies on Facebook for details — a dedicated website would help you rank much higher in local Google searches."
        elif site_platform == "linktree":
            return f"noticed {name_clean} uses Linktree as your main hub — a dedicated website would give you more space to showcase services and rank on Google."
        else:
            return f"noticed {name_clean} doesn't have an active website online yet — we could help set up a simple landing page to get you discovered by local clients."
            
    elif primary_def == "no_booking":
        platform_str = f"on {site_platform}" if site_platform and site_platform != "unknown" else "on the site"
        return f"looked at the website for {name_clean} and noticed there's no online booking widget {platform_str} — letting clients book 24/7 online usually saves tons of front-desk time."
        
    elif primary_def == "not_mobile":
        return f"checked out the site for {name_clean} on my phone and noticed it isn't fully mobile-optimized — fixing the mobile layout would make booking much smoother for clients."
        
    elif primary_def == "no_ssl":
        return f"noticed that the site for {name_clean} shows a 'Not Secure' warning because it's missing SSL — adding a security certificate would build instant trust with clients."
        
    elif primary_def == "dormant_social":
        return f"noticed the social links on the {name_clean} site haven't been updated recently — sharing recent before/afters or promos is a great way to keep clients engaged."
        
    elif primary_def == "no_social":
        return f"looked for social links on the {name_clean} site and didn't spot any — setting up a basic Instagram or Facebook page is a great way to showcase your results to new clients."
        
    # Default fallback
    return f"noticed {name_clean} has some great reviews online, but your web presence could be optimized to capture more local clients."

def generate_llm_hook(biz: Dict[str, Any]) -> str:
    """Generate a hook using Anthropic's Messages API."""
    if not config.ANTHROPIC_API_KEY:
        db.log_event(biz["place_id"], "hooks", "warn", "Anthropic API key missing, falling back to template hook.")
        return generate_template_hook(biz)
        
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    # Construct input context for LLM
    biz_input = {
        "name": biz.get("name"),
        "review_count": biz.get("review_count"),
        "rating": biz.get("rating"),
        "site_platform": biz.get("site_platform"),
        "has_online_booking": biz.get("has_online_booking"),
        "primary_deficiency": biz.get("primary_deficiency"),
        "social_status": biz.get("social_status"),
        "has_ssl": biz.get("has_ssl"),
        "is_mobile_friendly": biz.get("is_mobile_friendly")
    }
    
    system_prompt = (
        "You write a single, specific opening line for cold outreach to a med-spa owner.\n"
        "You may ONLY reference facts provided in INPUT. Do not invent details. No generic\n"
        "flattery. One or two sentences. Plain, human, lowercase-ok. State the observed gap\n"
        "and imply the fix without hard-pitching.\n\n"
        "Example INPUT:\n"
        "  name: \"Glow Med Spa\"\n"
        "  review_count: 612\n"
        "  rating: 4.9\n"
        "  site_platform: \"wix\"\n"
        "  has_online_booking: false\n"
        "  primary_deficiency: \"reviews_vs_web\"\n\n"
        "Example GOOD output:\n"
        "\"noticed glow has 600+ five-star reviews but no online booking on the site — you're probably losing after-hours bookings to whoever picks up the phone first.\"\n\n"
        "Example BAD output:\n"
        "\"I love what you're doing at Glow Med Spa! I help spas grow!\" (generic, no fact)"
    )
    
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": f"INPUT:\n{json.dumps(biz_input, indent=2)}"
            }
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=body, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        hook = data["content"][0]["text"].strip()
        # Remove surrounding quotes if generated
        if hook.startswith('"') and hook.endswith('"'):
            hook = hook[1:-1]
        return hook
    except Exception as e:
        db.log_event(biz["place_id"], "hooks", "error", f"Anthropic API call failed: {str(e)}. Falling back to template hook.")
        return generate_template_hook(biz)

def run_generate_hooks():
    """Generate hook text for all leads that lack hooks."""
    db.log_event(None, "hooks", "info", "Starting hook generation stage")
    businesses = db.get_businesses_to_enrich("hooks")
    print(f"Found {len(businesses)} businesses needing hooks.")
    
    generated_count = 0
    for biz in businesses:
        place_id = biz["place_id"]
        
        if config.MOCK_MODE:
            hook = generate_template_hook(biz)
        else:
            hook = generate_llm_hook(biz)
            
        updates = {
            "hook_text": hook,
            "hook_generated_at": datetime.datetime.now().isoformat()
        }
        db.update_business(place_id, updates)
        db.log_event(place_id, "hooks", "info", f"Generated hook: {hook}")
        generated_count += 1
        
    db.log_event(None, "hooks", "info", f"Completed hook generation stage for {generated_count} businesses")
    print(f"Generated hooks for {generated_count} businesses.")
