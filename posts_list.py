#!/usr/bin/env python3
"""List companies that came from LinkedIn posts (their Jobs rows have a Job ID starting with post-). Read-only."""
import fo.airtable as at


def main():
    comps = {c["_id"]: c for c in at.Companies.all()}
    seen = {}
    for j in at.Jobs.all():
        if str(j.get("Job ID", "")).startswith("post-"):
            for cid in j.get("Company LinkedIn URL") or []:
                seen.setdefault(cid, []).append(j)
    print(f"FROM POSTS: {len(seen)} companies (of {len(comps)} total)")
    for cid, js in sorted(seen.items(), key=lambda kv: (comps.get(kv[0], {}).get("Company") or "")):
        c = comps.get(cid, {})
        print(f"  {c.get('Company')} | {c.get('Status')} | last seen {c.get('Last seen')} | {js[0].get('Title','')[7:70]} | {js[0].get('Job URL','')[:90]}")


if __name__ == "__main__":
    main()
