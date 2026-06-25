"""Integration test: runs the full pipeline in mock mode and verifies output."""
import unittest
import os
import csv

# Ensure mock mode
os.environ["MOCK_MODE"] = "true"

from medspa_leads import config, db, export
from medspa_leads.stages.discover import discover_metro
from medspa_leads.stages.enrich_site import run_enrich_site
from medspa_leads.stages.enrich_booking import run_enrich_booking
from medspa_leads.stages.enrich_social import run_enrich_social
from medspa_leads.stages.enrich_contact import run_enrich_contact
from medspa_leads.stages.score import score_businesses, calculate_score
from medspa_leads.stages.hooks import run_generate_hooks

TEST_DB = "test_leads.db"
TEST_CSV = "test_review_queue.csv"

class TestFullPipeline(unittest.TestCase):

    def setUp(self):
        config.DB_PATH = TEST_DB
        config.MOCK_MODE = True
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        if os.path.exists(TEST_CSV):
            os.remove(TEST_CSV)

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        if os.path.exists(TEST_CSV):
            os.remove(TEST_CSV)

    def test_full_pipeline_single_metro(self):
        """Run the full pipeline for one metro and validate output."""
        db.init_db()

        count = discover_metro("Austin, TX", force=True)
        self.assertEqual(count, 7)

        run_enrich_site()
        run_enrich_booking()
        run_enrich_social()
        score_businesses()
        run_enrich_contact()
        run_generate_hooks()

        all_biz = db.get_all_businesses()
        self.assertEqual(len(all_biz), 7)

        # All scored
        for biz in all_biz:
            self.assertIsNotNone(biz["deficiency_score"])
            self.assertIsNotNone(biz["scored_at"])

        # All have site enrichment
        for biz in all_biz:
            self.assertIsNotNone(biz["site_fetched_at"])
            self.assertIsNotNone(biz["site_platform"])

        # All have booking enrichment
        for biz in all_biz:
            self.assertIsNotNone(biz["booking_checked_at"])

        # All have social enrichment
        for biz in all_biz:
            self.assertIsNotNone(biz["social_checked_at"])

        # All leads have hooks (no score gating)
        for biz in all_biz:
            self.assertIsNotNone(biz["hook_text"], f"Lead {biz['name']} should have a hook")
            self.assertGreater(len(biz["hook_text"]), 10, "Hook should be non-trivial")

    def test_csv_export(self):
        """Verify CSV export contains expected columns and data."""
        db.init_db()

        discover_metro("Austin, TX", force=True)
        run_enrich_site()
        run_enrich_booking()
        run_enrich_social()
        score_businesses()
        run_enrich_contact()
        run_generate_hooks()

        csv_count = export.export_to_csv(TEST_CSV)
        self.assertGreater(csv_count, 0)

        with open(TEST_CSV, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        self.assertEqual(len(rows), csv_count)

        expected_cols = ["name", "metro", "deficiency_score", "primary_deficiency", "phone", "hook_text"]
        for col in expected_cols:
            self.assertIn(col, rows[0].keys())

        # Rows sorted by score descending
        scores = [int(r["deficiency_score"]) for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_idempotent_rerun(self):
        """Verify that running discovery twice doesn't duplicate businesses."""
        db.init_db()

        discover_metro("Austin, TX", force=True)
        all_biz_1 = db.get_all_businesses()

        discover_metro("Austin, TX", force=True)
        all_biz_2 = db.get_all_businesses()

        self.assertEqual(len(all_biz_1), len(all_biz_2), "Rerun should not create duplicates")

    def test_scoring_no_website(self):
        """Verify that a business with no website gets high score."""
        biz = {
            "website_url": None,
            "site_platform": "none",
            "has_online_booking": 0,
            "is_mobile_friendly": 0,
            "has_ssl": 0,
            "social_status": "none",
            "review_count": 5
        }
        score, primary = calculate_score(biz)
        # no_website(3) + no_booking(2) + not_mobile(2) + no_ssl(1) + no_social(1) = 9
        self.assertEqual(score, 9)
        self.assertEqual(primary, "no_website")

if __name__ == "__main__":
    unittest.main()
