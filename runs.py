"""One row per keyword per fetch in the Airtable `Runs` table: what Apify returned, what was kept, what dedupe removed.
    python runs.py setup"""
import sys

from fo import airtable as at
from fo.airtable import _headers, base_id
from fo.http import request

Table = at.Table("Runs")
NUM = {"type": "number", "options": {"precision": 0}}
FIELDS = [{"name": "Run", "type": "singleLineText"}, {"name": "Source", "type": "singleLineText"}, {"name": "Keyword", "type": "singleLineText"},
          *({"name": n, **NUM} for n in ("Raw", "Kept", "Unique companies", "New", "Already listed")),
          {"name": "Dropped", "type": "multilineText"}]


def rows_for(run, source, by_query, dedupe):
    """by_query: {keyword: {total, kept, <drop reason>: n}}; dedupe: {keyword: {unique companies, new, already listed}}."""
    out = []
    for q in sorted(set(by_query) | set(dedupe)):
        s, d = by_query.get(q, {}), dedupe.get(q, {})
        drops = {k: v for k, v in s.items() if k not in ("total", "kept")}
        out.append({"Run": run, "Source": source, "Keyword": q or "(none)", "Raw": s.get("total", 0), "Kept": s.get("kept", 0),
                    "Unique companies": d.get("unique companies", 0), "New": d.get("new", 0), "Already listed": d.get("already listed", 0),
                    "Dropped": ", ".join(f"{k} {v}" for k, v in sorted(drops.items())) or "-"})
    return out


def log(rows, table=None):
    if rows:
        (table or Table).create(rows)
    return len(rows)


def setup():
    meta = f"https://api.airtable.com/v0/meta/bases/{base_id()}/tables"
    names = {t["name"] for t in request("GET", meta, headers=_headers()).json()["tables"]}
    if "Runs" not in names:
        request("POST", meta, headers=_headers(), json={"name": "Runs", "fields": FIELDS})
        print("created Runs table")


if __name__ == "__main__":
    setup() if sys.argv[1:] == ["setup"] else print(__doc__)
