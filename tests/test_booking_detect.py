import unittest
import os
from bs4 import BeautifulSoup

from medspa_leads.stages.enrich_booking import detect_booking_platform

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

class TestBookingDetection(unittest.TestCase):

    def _load_fixture(self, name):
        path = os.path.join(FIXTURES_DIR, name)
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        soup = BeautifulSoup(html, "html.parser")
        return html, soup

    def test_vagaro_detected(self):
        html, soup = self._load_fixture("vagaro_booking.html")
        has_booking, platform = detect_booking_platform(html, soup)
        self.assertEqual(has_booking, 1)
        self.assertEqual(platform, "vagaro")

    def test_mindbody_detected(self):
        html, soup = self._load_fixture("mindbody_booking.html")
        has_booking, platform = detect_booking_platform(html, soup)
        self.assertEqual(has_booking, 1)
        self.assertEqual(platform, "mindbody")

    def test_no_booking_detected(self):
        html, soup = self._load_fixture("no_booking.html")
        has_booking, platform = detect_booking_platform(html, soup)
        self.assertEqual(has_booking, 0)
        self.assertIsNone(platform)

if __name__ == "__main__":
    unittest.main()
