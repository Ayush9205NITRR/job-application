#!/usr/bin/env python3
"""Run the hiring-posts scrape on a small sample and show what the filters keep and drop. Writes nothing to Airtable.
    python posts_preview.py [--per 20]        (about $0.002 per post, needs APIFY_TOKEN)"""
import argparse
import re

import pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=20)
    a = ap.parse_args()
    items = pipeline.scrape_posts(pipeline.POST_QUERIES, a.per, "month", "date")
    jobs, st = pipeline.posts_to_jobs(items)
    print("STATS", st)
    print("KEPT (company | first line | post)")
    for j in jobs[:30]:
        print(f"  {j['company']} | {j['title'][7:90]} | {j['job_url'][:80]}")
    persons = [i for i in items if (i.get("author") or {}).get("type") != "company"]
    print(f"PEOPLE posts dropped: {len(persons)} (name | headline | first line)")
    for i in persons[:12]:
        au = i["author"]
        first = re.sub(r"\s+", " ", (i.get("content") or "").strip())[:80]
        print(f"  {au.get('name')} | {(au.get('info') or '')[:60]} | {first}")


if __name__ == "__main__":
    main()
