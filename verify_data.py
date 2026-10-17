#!/usr/bin/env python3
"""Read-only health check of the companies and jobs already in Airtable. Writes nothing.

Answers: is this data good enough for the Apollo match and the ICP filter?
  python verify_data.py [--base appXXXXXXXXXXXXXX]
Needs AIRTABLE_TOKEN (and AIRTABLE_BASE_ID unless --base is given).
"""
import argparse
import os
import re
import tempfile
from collections import Counter

from fo.rules import domain_of, norm_company_url


def slug(url):
    n = norm_company_url(url)
    return n.rsplit("/", 1)[1] if n else None


def analyse(comps):
    """-> dict of facts about the companies (each with a 'jobs' list). Pure, no network."""
    n = len(comps)
    slugs = [slug(c.get("Company LinkedIn URL")) for c in comps]
    valid = [s for s in slugs if s]
    dup = {s: k for s, k in Counter(valid).items() if k > 1}
    numeric = [s for s in valid if s.isdigit()]
    names = Counter(re.sub(r"[^a-z0-9]", "", (c.get("Company") or "").lower()) for c in comps)
    return {
        "companies": n, "jobs": sum(len(c.get("jobs", [])) for c in comps),
        "no_job": sum(1 for c in comps if not c.get("jobs")),
        "status": dict(Counter(c.get("Status") or "(blank)" for c in comps)),
        "valid_slug": len(valid), "numeric_slug": len(numeric), "duplicate_slugs": len(dup),
        "website": sum(1 for c in comps if domain_of(c.get("Website"))),
        "headcount": sum(1 for c in comps if c.get("Headcount")), "about": sum(1 for c in comps if c.get("About")),
        "ambiguous_names": sum(1 for v in names.values() if v > 1),
        "apollo_matched": sum(1 for c in comps if c.get("Apollo match")),
        "sample_urls": [c.get("Company LinkedIn URL") for c in comps[:4]],
    }


def roundtrip(comps):
    """Pretend Apollo exported every company with a differently written LinkedIn URL. Does the matcher still find all?"""
    import apollo_to_airtable as a
    variants = ["http://www.linkedin.com/company/{s}/", "https://linkedin.com/company/{S}", "https://in.linkedin.com/company/{s}?trk=x", "linkedin.com/company/{s}"]
    lines = ["Company Name,Company Linkedin Url,Funding Stage,# Employees"]
    for i, c in enumerate(comps):
        s = slug(c.get("Company LinkedIn URL"))
        if s:
            lines.append(f'"x{i}",{variants[i % 4].format(s=s, S=s.upper())},Seed,10')
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write("\n".join(lines) + "\n")
    try:
        apollo = a.load_apollo(f.name)
        matches, unmatched = a.match(apollo, comps)
    finally:
        os.unlink(f.name)
    return {"rows": len(lines) - 1, "matched": len(matches), "by_linkedin": sum(1 for m in matches if m[2] == "linkedin"), "unmatched": len(unmatched)}


def report(f, r, rt):
    n = r["companies"] or 1
    pct = lambda k: f"{r[k]} ({100 * r[k] // n}%)"
    out = [f"companies {r['companies']}, jobs {r['jobs']}, companies without a job {r['no_job']}",
           f"status: {r['status']}",
           f"LinkedIn slug usable {pct('valid_slug')}, numeric-only slug {r['numeric_slug']} (Apollo cannot match these by link), duplicate slugs {r['duplicate_slugs']}",
           f"website {pct('website')}, headcount {pct('headcount')}, about {pct('about')}, ambiguous company names {r['ambiguous_names']}",
           f"already enriched from Apollo: {r['apollo_matched']}",
           f"sample LinkedIn urls: {r['sample_urls']}",
           f"matcher round trip on this data: {rt['matched']}/{rt['rows']} matched ({rt['by_linkedin']} by LinkedIn link), {rt['unmatched']} missed"]
    verdict = []
    if r["numeric_slug"]:
        verdict.append(f"{r['numeric_slug']} companies have a numeric LinkedIn id: add Website via Apollo or they rely on name matching")
    if rt["unmatched"] or rt["matched"] < rt["rows"]:
        verdict.append("the matcher misses some LinkedIn urls, look at the sample urls")
    if r["website"] < n / 2:
        verdict.append("most companies have no website: the overlap check needs it, the Apollo Website column will fill it")
    if not r["apollo_matched"]:
        verdict.append("no Apollo data yet: the ICP filter has nothing to filter on until the Apollo CSV is saved")
    out.append("VERDICT: " + ("; ".join(verdict) if verdict else "data is ready for the ICP step"))
    text = "\n".join(out)
    print(text)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    a = ap.parse_args()
    if a.base:
        os.environ["AIRTABLE_BASE_ID"] = a.base
    import companies
    comps = companies.fetch()
    text = report(None, analyse(comps), roundtrip(comps))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Data check\n```\n" + text + "\n```\n")


if __name__ == "__main__":
    main()
