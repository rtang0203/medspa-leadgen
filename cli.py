"""CLI entry point: python3 -m cli run --metros "Austin,TX;Dallas,TX" """
import argparse
import sys
from medspa_leads import config
from medspa_leads import pipeline

def main():
    parser = argparse.ArgumentParser(
        description="Med-Spa Lead Discovery & Qualification Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 cli.py run --metros "Austin, TX;Dallas, TX"
  python3 cli.py run                          # uses default metros from config.py
  python3 cli.py run --force                  # force re-discovery even if cached
  python3 cli.py export                       # just re-export the review queue
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run the full pipeline")
    run_parser.add_argument(
        "--metros",
        type=str,
        default=None,
        help='Semicolon-separated list of metros. Example: "Austin, TX;Dallas, TX"'
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-discovery even if metros are cached"
    )
    
    # Export command
    subparsers.add_parser("export", help="Re-export the review queue from existing DB")
    
    args = parser.parse_args()
    
    if args.command == "run":
        if args.metros:
            metros = [m.strip() for m in args.metros.split(";") if m.strip()]
        else:
            metros = config.DEFAULT_METROS
            
        pipeline.run(metros, force_discover=args.force)
        
    elif args.command == "export":
        from medspa_leads import db
        from medspa_leads import export
        db.init_db()
        csv_count = export.export_to_csv()
        export.print_console_table()
        print(f"Exported {csv_count} leads to review_queue.csv")
        
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
