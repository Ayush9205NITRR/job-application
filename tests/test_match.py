import funnel
import match


def test_tiers():
    full = {"company": "Acme", "inter": ["Sprinto"], "text": "B2B SaaS", "stage": "Seed", "titles": ["Founder's Office"], "locations": ["Gurugram"], "trigger": True}
    n, hit = match.score(full)
    assert n == 6 and match.tier(n) == "personalised"
    line = match.opening_line(full)
    assert "Sprinto" in line and "Founder's Office" in line and "—" not in line
    assert match.tier(match.score({"company": "X", "stage": "Seed"})[0]) == "light"
    assert match.opening_line({"company": "X"}) == "" and match.tier(0) == "generic"


def test_light_never_claims_more_than_known():
    assert match.opening_line({"company": "X", "stage": "Seed"}) == ""


def test_funnel_counts():
    rows = [
        {"matched": True, "stage": "Seed", "funding_n": 2e6, "jobs": [{"url": "https://www.linkedin.com/jobs/view/1"}]},
        {"matched": True, "stage": "Pre-Seed", "funding_n": 0, "jobs": [{"url": "https://www.linkedin.com/posts/a_b"}]},
        {"matched": False, "stage": "", "funding_n": 0, "jobs": []},
    ]
    r = funnel.report(rows)
    assert r["unique accounts"] == 3 and r["verified (Apollo)"] == 2 and r["seed"] == 1 and r["pre-seed"] == 1
    assert r["funded"] == 1 and r["from job posts"] == 1 and r["from hiring posts"] == 1
