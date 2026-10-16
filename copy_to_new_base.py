#!/usr/bin/env python3
"""Copy Companies + Jobs from the current base (AIRTABLE_BASE_ID) into a NEW Airtable base.
The source base is only read, never changed. Run again with --existing-base to top up the copy.

    WORKSPACE_ID=wsp... python copy_to_new_base.py [--name "..."] [--dry-run]
    python copy_to_new_base.py --existing-base app...      # re-copy into a base made earlier

PAT needs schema.bases:read + schema.bases:write (create base) and data.records:read/write. If writing to the
new base is refused (403/404), add the new base to the token's access list in Airtable's token settings.
"""
import argparse
import os

from fo import airtable as at
from fo.airtable import _headers, base_id
from fo.http import request
from setup_airtable import SCHEMA

COPY = ("Companies", "Jobs")
LINK_FIELD = "Company LinkedIn URL"  # Jobs -> Companies


def base_id_of(raw):
    """Accepts an app... id or a pasted Airtable URL."""
    import re
    m = re.search(r"app[A-Za-z0-9]{14}", raw)
    if not m:
        raise SystemExit(f"not an Airtable base id or URL: {raw!r}")
    return m.group(0)


def plain(fields):
    return [f for f in fields if f["type"] != "multipleRecordLinks"]


def clean(rec, allowed):
    return {k: v for k, v in rec.items() if k in allowed and v not in (None, "", [])}


def translate_jobs(old_jobs, old_companies, new_id_by_url):
    """Jobs carry old company record ids; rewrite them to the new base's ids via the company URL key."""
    url_of = {c["_id"]: c.get(LINK_FIELD) for c in old_companies}
    allowed = {f["name"] for f in SCHEMA["Jobs"]}
    out, orphans = [], 0
    for j in old_jobs:
        row = clean(j, allowed - {LINK_FIELD})
        links = [new_id_by_url[url_of[i]] for i in j.get(LINK_FIELD) or [] if url_of.get(i) in new_id_by_url]
        if links:
            row[LINK_FIELD] = links
        else:
            orphans += 1
        out.append(row)
    return out, orphans


def create_base(name, workspace):
    tables = [{"name": t, "fields": plain(SCHEMA[t])} for t in COPY]
    r = request("POST", "https://api.airtable.com/v0/meta/bases", headers=_headers(),
                json={"name": name, "workspaceId": workspace, "tables": tables}).json()
    ids = {t["name"]: t["id"] for t in r["tables"]}
    request("POST", f"https://api.airtable.com/v0/meta/bases/{r['id']}/tables/{ids['Jobs']}/fields", headers=_headers(),
            json={"name": LINK_FIELD, "type": "multipleRecordLinks", "options": {"linkedTableId": ids["Companies"]}})
    return r["id"]


def ensure_tables(base):
    """Make sure Companies + Jobs exist in `base` with the right fields and the Jobs->Companies link.
    Only adds what is missing; never touches other tables (e.g. the default 'Table 1')."""
    meta = f"https://api.airtable.com/v0/meta/bases/{base}"
    get = lambda: {t["name"]: t for t in request("GET", meta + "/tables", headers=_headers()).json()["tables"]}
    tables = get()
    for name in COPY:
        have = {f["name"] for f in tables.get(name, {}).get("fields", [])}
        if name not in tables:
            print(f"creating table {name}")
            request("POST", meta + "/tables", headers=_headers(), json={"name": name, "fields": plain(SCHEMA[name])})
        else:
            for f in plain(SCHEMA[name]):
                if f["name"] not in have:
                    print(f"adding field {name}.{f['name']}")
                    request("POST", f"{meta}/tables/{tables[name]['id']}/fields", headers=_headers(), json=f)
    tables = get()
    if LINK_FIELD not in {f["name"] for f in tables["Jobs"]["fields"]}:
        print("adding link Jobs -> Companies")
        request("POST", f"{meta}/tables/{tables['Jobs']['id']}/fields", headers=_headers(),
                json={"name": LINK_FIELD, "type": "multipleRecordLinks",
                      "options": {"linkedTableId": tables["Companies"]["id"]}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Founder's Office Outbound (scraped copy)")
    ap.add_argument("--existing-base", help="copy into this base instead of creating one")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    companies, jobs = at.Companies.all(), at.Jobs.all()
    print(f"source base {base_id()}: {len(companies)} companies, {len(jobs)} jobs (read only)")
    if a.dry_run:
        print("dry run: nothing created")
        return
    new = a.existing_base and base_id_of(a.existing_base)
    if new:
        ensure_tables(new)
    else:
        ws = os.environ.get("WORKSPACE_ID", "").strip()
        if not ws.startswith("wsp"):
            raise SystemExit("WORKSPACE_ID (wsp...) is required to create a base")
        new = create_base(a.name, ws)
        print(f"created base {new}")
    NC, NJ = at.Table("Companies", base=new), at.Table("Jobs", base=new)
    c_allowed = {f["name"] for f in SCHEMA["Companies"]}
    saved = NC.upsert([clean(c, c_allowed) for c in companies], [LINK_FIELD])
    new_id_by_url = {r[LINK_FIELD]: r["_id"] for r in saved}
    rows, orphans = translate_jobs(jobs, companies, new_id_by_url)
    NJ.upsert(rows, ["Job ID"])
    print(f"copied {len(saved)} companies, {len(rows)} jobs ({orphans} jobs had no company link)")
    print(f"NEW BASE: https://airtable.com/{new}")


if __name__ == "__main__":
    main()
