import datetime
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, Optional, Protocol
from .. import config
from .. import db

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Regex for basic email matching
EMAIL_REGEX = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'

class EmailFinder(Protocol):
    def find(self, *, domain: str, business_name: str, owner_name: str | None) -> Dict[str, Any]:
        """Returns {'email': str|None, 'status': 'verified'|'guessed'|'not_found'|'not_attempted'}."""
        ...

class StubEmailFinder:
    """PoC default email finder that returns not_attempted."""
    def find(self, *, domain: str, business_name: str, owner_name: str | None) -> Dict[str, Any]:
        return {"email": None, "status": "not_attempted"}

class MockEmailFinder:
    """Mock email finder for testing/offline mode."""
    def find(self, *, domain: str, business_name: str, owner_name: str | None) -> Dict[str, Any]:
        # Generate clean mock emails based on domain name
        domain_clean = domain.replace("www.", "")
        if "radiantglow" in domain_clean:
            return {"email": "contact@radiantglow.com", "status": "verified"}
        elif "zendayspa" in domain_clean:
            return {"email": "info@zendayspa.com", "status": "guessed"}
        elif "classicderm" in domain_clean:
            return {"email": "hello@classicderm.com", "status": "verified"}
        elif "eliteskin" in domain_clean:
            return {"email": "info@eliteskin.com", "status": "verified"}
        elif "vanishmedspa" in domain_clean:
            return {"email": "bookings@vanishmedspa.com", "status": "verified"}
        return {"email": f"info@{domain_clean}", "status": "guessed"}

def get_email_finder() -> EmailFinder:
    """Returns the configured EmailFinder implementation."""
    if config.MOCK_MODE:
        return MockEmailFinder()
    return StubEmailFinder()

def extract_contact_form_and_emails(html: str, soup: BeautifulSoup, base_url: str) -> tuple[Optional[str], Optional[str]]:
    """Scan HTML and soup for contact forms and emails."""
    contact_form_url = None
    email = None
    
    # 1. Search for mailto: links and email addresses in page text
    for link in soup.find_all("a", href=True):
        href = link["href"]
        href_lower = href.lower()
        
        # Look for mailto
        if href_lower.startswith("mailto:"):
            email_match = href[7:].split("?")[0].strip()
            if re.match(EMAIL_REGEX, email_match):
                email = email_match
                
        # Look for contact pages
        if "contact" in href_lower or "reach-us" in href_lower or "get-in-touch" in href_lower:
            # Reconstruct absolute URL
            if not href_lower.startswith("http"):
                contact_form_url = urllib.parse.urljoin(base_url, href)
            else:
                contact_form_url = href
                
    # 2. If no email found in mailto, scan raw text using regex
    if not email:
        emails_found = re.findall(EMAIL_REGEX, html)
        # Filter out common false positives if any, take the first unique
        if emails_found:
            email = emails_found[0]
            
    return contact_form_url, email

def enrich_contact_details(biz: Dict[str, Any], email_finder: EmailFinder) -> Dict[str, Any]:
    """Find contact form and email for a business."""
    place_id = biz["place_id"]
    url = biz.get("website_url")
    name = biz.get("name")
    score = biz.get("deficiency_score") or 0
    
    updates = {
        "email": biz.get("email"),
        "email_status": biz.get("email_status") or "not_attempted",
        "notes": biz.get("notes")
    }
    
    # 1. Extract contact form and email from homepage HTML (cheap method)
    # Check if we can fetch the homepage and search for form & email
    contact_form = None
    scraped_email = None
    
    if url and not url.lower().startswith("https://facebook.com") and not url.lower().startswith("https://linktr.ee"):
        if config.MOCK_MODE:
            # Simulating scraping results for mock websites
            url_lower = url.lower()
            if "wordpress-outdated-medspa.com" in url_lower:
                contact_form = "http://wordpress-outdated-medspa.com/contact"
                scraped_email = "hello@classicderm.com"
            elif "wix-example-medspa.com" in url_lower:
                contact_form = "https://wix-example-medspa.com/contact-us"
            elif "squarespace-example-medspa.com" in url_lower:
                contact_form = "https://squarespace-example-medspa.com/connect"
        else:
            db.log_event(place_id, "enrich_contact", "info", f"Scanning {url} for contact form and email")
            try:
                headers = {"User-Agent": USER_AGENT}
                response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, "html.parser")
                contact_form, scraped_email = extract_contact_form_and_emails(response.text, soup, url)
                
            except Exception as e:
                db.log_event(place_id, "enrich_contact", "error", f"Error scanning contact details on website: {str(e)}")
                
    if contact_form:
        updates["notes"] = f"Contact form found: {contact_form}"
        db.log_event(place_id, "enrich_contact", "info", f"Found contact form for {name}: {contact_form}")
        
    if scraped_email:
        updates["email"] = scraped_email
        updates["email_status"] = "verified"
        db.log_event(place_id, "enrich_contact", "info", f"Found email via site scrape for {name}: {scraped_email}")
        
    # 2. If score warrants and email still not found, call EmailFinder
    if score >= config.GOOD_LEAD_THRESHOLD and not updates["email"]:
        # Extract domain from url
        domain = ""
        if url:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc or parsed.path.split("/")[0]
            
        if domain and not domain.startswith("facebook.com") and not domain.startswith("linktr.ee"):
            db.log_event(place_id, "enrich_contact", "info", f"Calling EmailFinder for domain: {domain}")
            result = email_finder.find(domain=domain, business_name=name, owner_name=None)
            
            updates["email"] = result.get("email")
            updates["email_status"] = result.get("status", "not_found")
            db.log_event(place_id, "enrich_contact", "info", f"EmailFinder status for {name}: {updates['email_status']}")
        else:
            # If facebook/linktree, we might use default mock/stub
            if config.MOCK_MODE:
                # Generate a mock email for facebook/linktree too if they are high scorers
                fake_domain = name.lower().replace(" ", "") + ".com"
                result = email_finder.find(domain=fake_domain, business_name=name, owner_name=None)
                updates["email"] = result.get("email")
                updates["email_status"] = result.get("status", "not_found")
            else:
                updates["email_status"] = "not_found"
                
    return updates

def run_enrich_contact():
    """Run contact enrichment for all businesses in the DB."""
    db.log_event(None, "enrich_contact", "info", "Starting contact enrichment stage")
    # Fetch businesses. Since we do direct scrape + API lookup, we look for 'not_attempted' status
    businesses = db.get_businesses_to_enrich("enrich_contact")
    print(f"Found {len(businesses)} businesses needing contact enrichment.")
    
    email_finder = get_email_finder()
    enriched_count = 0
    
    for biz in businesses:
        updates = enrich_contact_details(biz, email_finder)
        db.update_business(biz["place_id"], updates)
        enriched_count += 1
        
    db.log_event(None, "enrich_contact", "info", f"Completed contact enrichment stage for {enriched_count} businesses")
    print(f"Enriched contact info for {enriched_count} businesses.")
