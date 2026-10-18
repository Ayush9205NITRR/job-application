"""Which source finds real startups? Per Companies.Source: how many companies, how many survived classification.
    python scorecard.py            print; a source under 20 percent (with 20+ companies) goes to 'Your tasks' as 'review this source'
A new source starts in a trial: run it small, read this, then widen."""
import sys
from collections import Counter

import alerts
from fo import airtable as at

MIN_ROWS, MIN_RATE = 20, 0.20
DEAD = ("Excluded",)


def score(companies):
    seen, kept = Counter(), Counter()
    for c in companies:
        s = c.get("Source") or "untagged"
        seen[s] += 1
        if c.get("Status") not in (*DEAD, "New", None, ""):
            kept[s] += 1
    return {s: {"companies": seen[s], "kept": kept[s], "rate": round(kept[s] / seen[s], 2)} for s in seen}


def weak(card):
    return [s for s, v in card.items() if v["companies"] >= MIN_ROWS and v["rate"] < MIN_RATE and s != "untagged"]


def main():
    card = score(at.Companies.all())
    for s, v in sorted(card.items()):
        print(f"{s}: {v['companies']} companies, {v['kept']} kept ({int(v['rate'] * 100)}%)")
    for s in weak(card):
        alerts.raise_alert(f"weak_source_{s}", f"Source {s} gives few real startups ({int(card[s]['rate'] * 100)}%)",
                           "1. Look at its Companies rows with Status Excluded.\n2. Tighten its query or stop using it.\n3. Set this row to Done.")
    return card


if __name__ == "__main__":
    main()
