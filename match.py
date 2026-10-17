"""Stage 8-9 of Canvas A: score how well a company fits Ayush's profile (6 parameters) and pick the opening line.
Pure code, no LLM. 3+ hits: personalised; 1-2: light; 0: the generic template line (build() default)."""
import re

import overlap

INDUSTRIES = re.compile(r"saas|software|fintech|financ|real estate|proptech|b2b|marketing|advertis|adtech|startup|technology", re.I)
STAGE = re.compile(r"pre.?seed|seed|angel", re.I)
ROLE = re.compile(r"founder.?s? office|chief of staff|business development|\bbd\b|\bsales\b|growth|partnership", re.I)
CITY = re.compile(r"gurugram|gurgaon|delhi|noida|ncr|remote", re.I)
PARAMS = ("client", "industry", "stage", "role", "city", "trigger")


def score(c):
    """c: dict with inter (matched client names), text (about/industry), stage, titles (list), locations (list), trigger (bool)."""
    hit = {
        "client": bool(c.get("inter")),
        "industry": bool(INDUSTRIES.search(c.get("text") or "")),
        "stage": bool(STAGE.search(c.get("stage") or "")),
        "role": any(ROLE.search(t or "") for t in c.get("titles") or []),
        "city": any(CITY.search(l or "") for l in c.get("locations") or []),
        "trigger": bool(c.get("trigger")),
    }
    return sum(hit.values()), hit


def tier(n):
    return "personalised" if n >= 3 else "light" if n >= 1 else "generic"


def opening_line(c):
    """Only claims what is known: a client Ayush supplied, or a post/role the company itself published."""
    n, hit = score(c)
    parts = []
    if hit["client"]:
        parts.append(overlap.pitch_line(c["company"], c["inter"]))
    if hit["role"] and n >= 3:
        parts.append(f"I saw {c['company']} is hiring for {next(t for t in c['titles'] if ROLE.search(t or ''))}.")
    elif hit["trigger"] and n >= 3:
        parts.append(f"I saw {c['company']} is hiring.")
    if not hit["client"] and hit["stage"] and n >= 3:
        parts.append("I like working with teams at the seed stage.")
    return " ".join(parts[:2]) if n >= 1 else ""
