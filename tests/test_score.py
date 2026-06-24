import unittest

from medspa_leads.stages.score import calculate_score

class TestScoring(unittest.TestCase):
    
    def test_no_website_places_data_empty(self):
        # Business with no website url at all
        biz = {
            "website_url": None,
            "site_platform": None,
            "review_count": 10
        }
        score, primary = calculate_score(biz)
        self.assertEqual(score, 3) # no_website weight = 3
        self.assertEqual(primary, "no_website")
        
    def test_no_website_places_data_linktree(self):
        # Business with linktree URL in Places data
        biz = {
            "website_url": "https://linktr.ee/spa_name",
            "site_platform": None,
            "review_count": 5
        }
        score, primary = calculate_score(biz)
        self.assertEqual(score, 3)
        self.assertEqual(primary, "no_website")

    def test_no_website_places_data_facebook(self):
        # Business with facebook URL in Places data
        biz = {
            "website_url": "https://facebook.com/spa_name",
            "site_platform": None,
            "review_count": 5
        }
        score, primary = calculate_score(biz)
        self.assertEqual(score, 3)
        self.assertEqual(primary, "no_website")

    def test_no_booking(self):
        # Business with website, but has_online_booking is 0 (False)
        biz = {
            "website_url": "https://goodspa.com",
            "site_platform": "wordpress",
            "has_online_booking": 0,
            "is_mobile_friendly": 1,
            "has_ssl": 1,
            "review_count": 20
        }
        score, primary = calculate_score(biz)
        self.assertEqual(score, 2) # no_booking weight = 2
        self.assertEqual(primary, "no_booking")

    def test_reviews_vs_web_bonus(self):
        # Business with review_count >= 100 and no website
        # Should get no_website (3) + reviews_vs_web bonus (2) = 5
        # Primary deficiency should override to reviews_vs_web
        biz = {
            "website_url": None,
            "site_platform": None,
            "review_count": 150
        }
        score, primary = calculate_score(biz)
        self.assertEqual(score, 5)
        self.assertEqual(primary, "reviews_vs_web")
        
    def test_reviews_vs_web_not_triggered_low_reviews(self):
        # Business with review_count < 100 and no website
        # Should get no_website (3) only.
        biz = {
            "website_url": None,
            "site_platform": None,
            "review_count": 99
        }
        score, primary = calculate_score(biz)
        self.assertEqual(score, 3)
        self.assertEqual(primary, "no_website")

    def test_multiple_deficiencies(self):
        # Business with:
        # - no_booking (2)
        # - not_mobile (2)
        # - no_ssl (1)
        # - review_count = 120 (adds reviews_vs_web bonus +2 because of no_booking and not_mobile)
        # Total = 2 + 2 + 1 + 2 = 7
        # Primary should be reviews_vs_web
        biz = {
            "website_url": "https://poor-web.com",
            "site_platform": "custom",
            "has_online_booking": 0,
            "is_mobile_friendly": 0,
            "has_ssl": 0,
            "review_count": 120
        }
        score, primary = calculate_score(biz)
        self.assertEqual(score, 7)
        self.assertEqual(primary, "reviews_vs_web")

if __name__ == "__main__":
    unittest.main()
