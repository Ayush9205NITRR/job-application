#!/usr/bin/env python3
"""Top of the funnel, one command: LinkedIn hiring posts AND LinkedIn jobs from Apify, per keyword.
Dedupe is on the company LinkedIn URL: a company already in Airtable is never added twice. Every keyword gets a row in Runs.
    python fetch.py [--per 50] [--no-jobs]      (posts about $0.002 each; spends Apify credit)"""
import argparse
from collections import Counter

import alerts
import pipeline
import runs
import tasks as T
from fo import airtable as at
from fo import rules


def jobs_stats(raw):
    """Jobs path has no drop reasons of its own: count raw and 'kept' (has a company URL) per keyword."""
    by = {}
    for r in raw:
        n = pipeline.normalise(r)
        c = by.setdefault(n["query"], Counter())
        c["total"] += 1
        c["kept" if rules.norm_company_url(n["company_url"]) else "no company link"] += 1
    return {q: dict(c) for q, c in by.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=50)
    ap.add_argument("--no-jobs", action="store_true")
    a = ap.parse_args()
    run = T.stamp()[:16].replace("T", " ")
    rows = []
    with alerts.guard():
        posts = pipeline.scrape_posts(pipeline.POST_QUERIES, a.per, "month", "date")
        r = pipeline.run_posts(posts, only_new=True)
        st = r.get("posts", {})
        rows += runs.rows_for(run, "linkedin_posts", st.get("by_query", {}), st.get("dedupe", {}))
        print("POSTS", {k: v for k, v in r.items() if k != "posts"})
        if not a.no_jobs:
            raw = pipeline.scrape(limit=a.per)
            listed = {c.get("Company LinkedIn URL") for c in at.Companies.all()}
            dd = pipeline.dedupe_stats([pipeline.normalise(x) for x in raw], listed)
            print("JOBS", pipeline.run(raw))
            rows += runs.rows_for(run, "linkedin_jobs", jobs_stats(raw), dd)
    print("runs logged", runs.log(rows))


if __name__ == "__main__":
    main()
