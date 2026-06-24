"""Pipeline orchestrator — the run() function from §3 of the spec."""
from . import db
from .stages.discover import discover_metro
from .stages.enrich_site import run_enrich_site
from .stages.enrich_booking import run_enrich_booking
from .stages.enrich_social import run_enrich_social
from .stages.enrich_contact import run_enrich_contact
from .stages.score import score_businesses
from .stages.hooks import run_generate_hooks
from . import export

def run(metros: list[str], force_discover: bool = False):
    """
    Main pipeline entry point.
    
    1. Discover businesses in each metro via Places API
    2. Enrich: website analysis, booking detection, social signals
    3. Score all businesses
    4. Enrich contacts (gated by score for email API)
    5. Generate hooks for qualified leads
    6. Export review queue
    """
    print("=" * 60)
    print("Med-Spa Lead Discovery & Qualification Pipeline")
    print("=" * 60)
    
    # Initialize database
    db.init_db()
    
    # Step 1: Discover businesses in each metro
    print("\n--- Stage 1: Discovery ---")
    total_discovered = 0
    for metro in metros:
        count = discover_metro(metro, force=force_discover)
        total_discovered += count
    print(f"Discovery complete. {total_discovered} businesses upserted.\n")
    
    # Step 2: Enrich website details
    print("--- Stage 2: Website Enrichment ---")
    run_enrich_site()
    
    # Step 3: Enrich booking details
    print("\n--- Stage 3: Booking Enrichment ---")
    run_enrich_booking()
    
    # Step 4: Enrich social signals
    print("\n--- Stage 4: Social Enrichment ---")
    run_enrich_social()
    
    # Step 5: Score all businesses
    print("\n--- Stage 5: Scoring ---")
    score_businesses()
    
    # Step 6: Enrich contacts (gated by score)
    print("\n--- Stage 6: Contact Enrichment ---")
    run_enrich_contact()
    
    # Step 7: Generate hooks for qualified leads
    print("\n--- Stage 7: Hook Generation ---")
    run_generate_hooks()
    
    # Step 8: Export review queue
    print("\n--- Stage 8: Export ---")
    csv_count = export.export_to_csv()
    export.print_console_table()
    
    print("=" * 60)
    print(f"Pipeline complete. {csv_count} leads exported to review_queue.csv")
    print("=" * 60)
