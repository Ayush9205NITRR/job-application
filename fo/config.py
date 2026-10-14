import os
import sys
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # dotenv optional in tests
    pass


def env(name: str, default: str | None = None, required: bool = True) -> str:
    val = os.environ.get(name, default)
    if not val and required:
        sys.exit(f"Missing env var {name}. Copy .env.example to .env and fill it in.")
    return val or ""


# Business rules (spec sections 5 and 7)
SHORTLIST_SCORE = 60
REVIEW_SCORE = 45  # 45-59 -> Needs review, below -> Excluded
MAX_CONTACTS_PER_COMPANY = 2
FOUNDER_TITLES = ["Founder", "Co-Founder", "CEO", "COO", "Chief of Staff", "Head of Growth"]
MAX_DAILY_EMAILS = 20
RAMP = [(7, 5), (14, 10), (21, 15)]  # (days since start <, cap); afterwards MAX_DAILY_EMAILS
BOUNCE_STOP_RATE = 0.03
BOUNCE_MIN_SAMPLE = 10
FOLLOWUP_DAYS = {2: 5, 3: 12}
