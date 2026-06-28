import csv
import sys
from typing import List, Dict, Any
from . import db
from . import config

def get_qualified_leads() -> List[Dict[str, Any]]:
    """Fetch all leads from the database, ordered by score descending."""
    conn = db.get_db_connection()
    cursor = conn.cursor()
    
    # Select all scored businesses, ordered by score
    cursor.execute(
        """
        SELECT * FROM businesses 
        WHERE review_status = 'new'
        ORDER BY deficiency_score DESC, review_count DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def export_to_csv(filepath: str = "review_queue.csv") -> int:
    """Export qualified leads to a CSV file."""
    leads = get_qualified_leads()
    if not leads:
        db.log_event(None, "export", "warn", "No leads found to export.")
        
    headers = [
        "name", "metro", "deficiency_score", "primary_deficiency", 
        "phone", "email", "email_status", "website_url", 
        "booking_platform", "social_status", "hook_text", "google_maps_url"
    ]
    
    try:
        with open(filepath, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            
            for lead in leads:
                # Construct google_maps_url from place_id
                google_maps_url = ""
                if lead.get("place_id"):
                    # Use a standard search query by place_id or standard URL format
                    google_maps_url = f"https://www.google.com/maps/place/?q=place_id:{lead['place_id']}"
                
                row_data = {
                    "name": lead.get("name"),
                    "metro": lead.get("metro"),
                    "deficiency_score": lead.get("deficiency_score"),
                    "primary_deficiency": lead.get("primary_deficiency"),
                    "phone": lead.get("phone"),
                    "email": lead.get("email"),
                    "email_status": lead.get("email_status"),
                    "website_url": lead.get("website_url"),
                    "booking_platform": lead.get("booking_platform"),
                    "social_status": lead.get("social_status"),
                    "hook_text": lead.get("hook_text"),
                    "google_maps_url": google_maps_url
                }
                writer.writerow(row_data)
                
        db.log_event(None, "export", "info", f"Exported {len(leads)} leads to {filepath}")
        return len(leads)
    except Exception as e:
        db.log_event(None, "export", "error", f"Failed to export CSV: {str(e)}")
        print(f"Error exporting CSV: {e}", file=sys.stderr)
        return 0

def print_console_table() -> None:
    """Print a clean, readable console table of all discovered leads."""
    leads = get_qualified_leads()
    if not leads:
        print("\nNo leads in the queue.")
        return
        
    print(f"\n--- Ranked Review Queue ({len(leads)} Leads) ---")
    
    # Format columns: Name (22), Metro (12), Score (5), Primary Deficiency (20), Phone (14)
    header_fmt = "| {name:<22} | {metro:<12} | {score:<5} | {deficiency:<20} | {phone:<14} |"
    divider = "-" * 85
    
    print(divider)
    print(header_fmt.format(
        name="Name", metro="Metro", score="Score", deficiency="Primary Def.", phone="Phone"
    ))
    print(divider)
    
    for lead in leads:
        name = lead.get("name") or ""
        if len(name) > 22:
            name = name[:19] + "..."
            
        metro = lead.get("metro") or ""
        if len(metro) > 12:
            metro = metro[:9] + "..."
            
        score = str(lead.get("deficiency_score") or 0)
        
        deficiency = lead.get("primary_deficiency") or "none"
        if len(deficiency) > 20:
            deficiency = deficiency[:17] + "..."
            
        phone = lead.get("phone") or "N/A"
        if len(phone) > 14:
            phone = phone[:11] + "..."
            
        print(header_fmt.format(
            name=name, metro=metro, score=score, deficiency=deficiency, phone=phone
        ))
        
    print(divider)
    print("Run `python3 cli.py export` to dump the full queue to review_queue.csv.\n")
