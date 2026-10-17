#!/usr/bin/env python3
"""Scrape hiring posts and add ONLY companies not already in Airtable (existing rows are untouched).
    python posts_push.py [--per 50]        (about $0.002 per post, needs APIFY_TOKEN + AIRTABLE_TOKEN)"""
import argparse

import pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=50)
    a = ap.parse_args()
    items = pipeline.scrape_posts(pipeline.POST_QUERIES, a.per, "month", "date")
    r = pipeline.run_posts(items, only_new=True)
    print("RESULT", r)


if __name__ == "__main__":
    main()
