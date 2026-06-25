import os
from dotenv import load_dotenv

# Load environment variables from .env 
load_dotenv()

# Database Config
DB_PATH = os.getenv("DB_PATH", "leads.db")

# API Keys
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Mock Mode: If keys are missing, default to True
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() in ("true", "1", "yes") or not (GOOGLE_PLACES_API_KEY and ANTHROPIC_API_KEY)

# Default Metros
DEFAULT_METROS = [
    "Chicago, IL",
    "Milwaukee, WI",
    "Minneapolis, MN",
    "Austin, TX",
]

# Scoring Weights
WEIGHTS = {
    "no_website": 3,
    "no_booking": 2,
    "not_mobile": 2,
    "no_ssl": 1,
    "dormant_social": 1,
    "no_social": 1,
}

# Threshold for "Good Lead"
GOOD_LEAD_THRESHOLD = 4
