#!/usr/bin/env python3
"""Copy the post-sourced companies (and their post- Jobs) from the OLD base into the working base (AIRTABLE_BASE_ID).
Create-only: companies already in the working base are left exactly as they are. The old base is only read.
    python move_posts.py [--old app40Dtij4xm0M2aW] [--dry-run]"""
import argparse

from fo import airtable as at
from setup_airtable import SCHEMA

LINK = "Company LinkedIn URL"


def pick(old_companies, old_jobs, have_urls):
    """-> (company rows to create, job rows with the old company id kept for relinking)."""
    post_jobs = [j for j in old_jobs if str(j.get("Job ID", "")).startswith("post-")]
    ids = {i for j in post_jobs for i in j.get(LINK) or []}
    comps = [c for c in old_companies if c["_id"] in ids and c.get(LINK) not in have_urls]
    return comps, post_jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="app40Dtij4xm0M2aW")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    OC, OJ = at.Table("Companies", base=a.old), at.Table("Jobs", base=a.old)
    old_c, old_j = OC.all(), OJ.all()
    have = {c.get(LINK): c for c in at.Companies.all()}
    comps, post_jobs = pick(old_c, old_j, set(have))
    print(f"to copy: {len(comps)} companies, {len(post_jobs)} post jobs (already in working base: {len(have)} companies)")
    if a.dry_run:
        return
    allowed = {f["name"] for f in SCHEMA["Companies"]}
    rows = [{k: v for k, v in c.items() if k in allowed and v not in (None, "", [])} for c in comps]
    saved = at.Companies.create(rows) if rows else []
    new_id = {r[LINK]: r["_id"] for r in saved} | {u: c["_id"] for u, c in have.items()}
    url_of = {c["_id"]: c.get(LINK) for c in old_c}
    j_allowed = {f["name"] for f in SCHEMA["Jobs"]} - {LINK}
    have_jobs = {j.get("Job ID") for j in at.Jobs.all()}
    out = []
    for j in post_jobs:
        if j.get("Job ID") in have_jobs:
            continue
        row = {k: v for k, v in j.items() if k in j_allowed and v not in (None, "", [])}
        links = [new_id[url_of[i]] for i in j.get(LINK) or [] if url_of.get(i) in new_id]
        if links:
            row[LINK] = links
        out.append(row)
    if out:
        at.Jobs.create(out)
    print(f"created {len(saved)} companies and {len(out)} jobs in the working base")


if __name__ == "__main__":
    main()
