#!/usr/bin/env python3
"""Create (or top up) the six Airtable tables from the spec. Safe to re-run: only adds what is missing.

Needs a PAT with scopes: schema.bases:read, schema.bases:write (plus data scopes for the other scripts).
    python setup_airtable.py --dry-run
    python setup_airtable.py
"""
import argparse

from fo.airtable import _headers, base_id
from fo.http import request

T = lambda n: {"name": n, "type": "singleLineText"}
LONG = lambda n: {"name": n, "type": "multilineText"}
URL = lambda n: {"name": n, "type": "url"}
NUM = lambda n: {"name": n, "type": "number", "options": {"precision": 0}}
CHECK = lambda n: {"name": n, "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}}
DT = lambda n: {"name": n, "type": "dateTime", "options": {
    "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "utc"}}
SEL = lambda n, *opts: {"name": n, "type": "singleSelect", "options": {"choices": [{"name": o} for o in opts]}}
LINK = lambda n, table: {"name": n, "type": "multipleRecordLinks", "link_to": table}

# The first field of each table is its primary field.
SCHEMA = {
    "Companies": [
        T("Company LinkedIn URL"), T("Company"), URL("Website"), T("Headcount"), T("Startup type"),
        CHECK("B2B"), T("Sales motion"), T("Stage (guess)"), NUM("ICP score"), LONG("ICP reason"),
        SEL("Status", "Needs review", "Shortlisted", "Excluded", "Enriched", "Applied", "Contacted", "Replied", "Closed"),
        {"name": "Last seen", "type": "date", "options": {"dateFormat": {"name": "iso"}}},
        LONG("About"),
    ],
    "Jobs": [
        T("Job ID"), T("Title"), URL("Job URL"), T("Posted"), T("Location"), T("Poster"), URL("Poster LinkedIn URL"),
        LINK("Company LinkedIn URL", "Companies"), CHECK("Applied"),
    ],
    "Contacts": [
        T("Email"), T("First name"), T("Last name"), T("Title"), URL("LinkedIn URL"), LINK("Company", "Companies"),
        SEL("Email status", "verified", "risky", "invalid"),
        SEL("Stage", "New", "Drafted", "Approved", "Sent", "Followed up", "Replied", "Bounced", "Do not contact"),
        SEL("Contact type", "Founder", "Job poster"), LONG("First line"),
    ],
    "Touches": [
        T("Subject"), LINK("Contact", "Contacts"), SEL("Channel", "Email", "LinkedIn invite", "LinkedIn message"),
        T("Template"), NUM("Step"), LONG("Body"), DT("Drafted at"), DT("Sent at"), T("Gmail thread ID"),
    ],
    "Replies": [
        T("Summary"), LINK("Contact", "Contacts"), DT("Received at"), LONG("Body"),
        SEL("Class", "Interested", "Not now", "Referral", "Not a fit", "OOO", "Unsubscribe"),
        LONG("Suggested response"), CHECK("Handled"),
    ],
    "Templates": [
        T("Name"), SEL("Channel", "Email", "LinkedIn invite", "LinkedIn message"), NUM("Step"), T("Subject"),
        LONG("Body"), SEL("Variant", "A", "B"), NUM("Sends"), NUM("Replies"),
    ],
}


def meta_url(*parts):
    return "/".join(["https://api.airtable.com/v0/meta/bases", base_id(), *parts])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tables = {t["name"]: t for t in request("GET", meta_url("tables"), headers=_headers()).json()["tables"]}
    plain = lambda fs: [f for f in fs if f["type"] != "multipleRecordLinks"]

    for name, fields in SCHEMA.items():
        have = {f["name"] for f in tables.get(name, {}).get("fields", [])}
        if name not in tables:
            print(f"create table {name}")
            if not args.dry_run:
                tables[name] = request("POST", meta_url("tables"), headers=_headers(),
                                       json={"name": name, "fields": plain(fields)}).json()
            continue
        for f in plain(fields):
            if f["name"] not in have:
                print(f"add field {name}.{f['name']}")
                if not args.dry_run:
                    request("POST", meta_url("tables", tables[name]["id"], "fields"), headers=_headers(), json=f)

    # Link fields last: they need the target table to exist. Airtable adds the inverse field itself.
    for name, fields in SCHEMA.items():
        if name not in tables:
            continue
        have = {f["name"] for f in tables[name].get("fields", [])}
        for f in fields:
            if f["type"] == "multipleRecordLinks" and f["name"] not in have:
                print(f"add link {name}.{f['name']} -> {f['link_to']}")
                if not args.dry_run:
                    body = {"name": f["name"], "type": f["type"],
                            "options": {"linkedTableId": tables[f["link_to"]]["id"]}}
                    request("POST", meta_url("tables", tables[name]["id"], "fields"), headers=_headers(), json=body)
    print("done" if not args.dry_run else "dry run: nothing created")


if __name__ == "__main__":
    main()
