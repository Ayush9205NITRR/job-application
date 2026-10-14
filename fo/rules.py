"""Pure business rules: no network, fully unit-tested."""
import re
from datetime import date, datetime, timedelta

from . import config

# --- URLs / dedupe ---------------------------------------------------------

def norm_company_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"linkedin\.com/company/([^/?#]+)", url, re.I)
    return f"https://www.linkedin.com/company/{m.group(1).lower()}" if m else None


def domain_of(website: str | None) -> str | None:
    if not website:
        return None
    d = re.sub(r"^https?://", "", website.strip().lower()).split("/")[0]
    return re.sub(r"^www\.", "", d) or None


def group_by_company(jobs: list[dict]) -> dict[str, list[dict]]:
    """jobs must carry 'company_url'. Drops jobs without a valid company URL and duplicate job ids."""
    out: dict[str, list[dict]] = {}
    seen = set()
    for j in jobs:
        url = norm_company_url(j.get("company_url"))
        if not url or j.get("job_id") in seen:
            continue
        seen.add(j.get("job_id"))
        out.setdefault(url, []).append(j)
    return out


# --- Hard excludes (spec section 7) ----------------------------------------

_EXCLUDE = [
    ("MICE/events", r"\bMICE\b|event (management|planning|company|agency)|events? & exhibitions?|exhibitions?\b|wedding"),
    ("staffing/recruitment", r"staffing|recruit(ment|ing|er)s?\b|manpower|headhunt|executive search|placement|talent (acquisition )?(firm|agency|solutions)"),
    ("assistant role", r"(executive|personal|virtual|administrative) assistant|\bEA to\b|secretary"),
]


def hard_exclude(company_name: str, title: str = "", industry: str = "") -> str | None:
    """Return the exclusion reason, or None. Checked on company name, job title, industry only."""
    hay = f"{company_name} | {title} | {industry}"
    for reason, pat in _EXCLUDE:
        if re.search(pat, hay, re.I):
            return reason
    return None


# --- ICP score (spec section 7) --------------------------------------------

ICP_MAX = {"b2b": 30, "sales_motion": 20, "role": 20, "stage_size": 20, "location": 10}


def icp_total(parts: dict) -> int:
    return sum(max(0, min(int(parts.get(k, 0) or 0), cap)) for k, cap in ICP_MAX.items())


def status_for_score(score: int) -> str:
    if score >= config.SHORTLIST_SCORE:
        return "Shortlisted"
    return "Needs review" if score >= config.REVIEW_SCORE else "Excluded"


# Once a company moves past classification, re-runs must never reset it.
ADVANCED = {"Enriched", "Applied", "Contacted", "Replied", "Closed"}


# --- Sending caps (spec section 5) -----------------------------------------

def daily_cap(start: date | None, today: date) -> int:
    if start is None:
        return config.RAMP[0][1]
    days = (today - start).days
    for limit, cap in config.RAMP:
        if days < limit:
            return cap
    return config.MAX_DAILY_EMAILS


def bounce_halt(sent: int, bounced: int) -> bool:
    if bounced >= 2:
        return True
    return sent >= config.BOUNCE_MIN_SAMPLE and bounced / sent > config.BOUNCE_STOP_RATE


# --- Follow-ups ------------------------------------------------------------

BLOCKED_STAGES = {"Replied", "Bounced", "Do not contact"}


def followup_step_due(step1_sent: datetime, now: datetime, existing_steps: set[int]) -> int | None:
    """Next follow-up step (2 or 3) that is due, else None. Step 3 needs a step 2 touch."""
    age = now - step1_sent
    if 2 not in existing_steps and age >= timedelta(days=config.FOLLOWUP_DAYS[2]):
        return 2
    if 2 in existing_steps and 3 not in existing_steps and age >= timedelta(days=config.FOLLOWUP_DAYS[3]):
        return 3
    return None


# --- Copy checks -----------------------------------------------------------

_FLATTERY = re.compile(r"\b(impressive|amazing|incredible|inspiring|love what|admire|great work|fantastic|exciting|passionate)\b", re.I)
# Health details must never appear anywhere (spec). Conservative keyword lint.
_HEALTH = re.compile(r"\b(health|medical|illness|disease|diagnos\w*|disabilit\w*|surgery|therapy|medication|cancer|diabet\w*|depress\w*|anxiety|hospital|treatment)\b", re.I)


def check_first_line(s: str) -> str | None:
    """Return a problem description, or None if the line is acceptable."""
    s = (s or "").strip()
    if not s:
        return "empty"
    if "—" in s or "–" in s:
        return "contains dash"
    if len(s.split()) >= 20:
        return "20 words or more"
    if "\n" in s:
        return "more than one line"
    if _FLATTERY.search(s):
        return "flattery"
    if _HEALTH.search(s):
        return "health term"
    return None


def has_health_terms(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _HEALTH.finditer(text or "")})
