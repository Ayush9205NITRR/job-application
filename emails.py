"""Mailing without the UI: code writes ready-to-send emails into an Airtable `Emails` table, the agent pastes each one into a
Gmail DRAFT (never sends), and Ayush presses Send himself. Gate 1 is the `Shortlist` tick on a company in Airtable,
gate 2 is Ayush pressing Send in Gmail.

    python emails.py setup     create the Emails table and the Companies.Shortlist checkbox (additive)
    python emails.py build     one Emails row per verified contact of a shortlisted company (never twice for a contact)
    python emails.py stats     how many rows are in which state
"""
import sys

from fo import airtable as at
from fo.airtable import _headers, base_id
from fo.http import request

import outbound as ob

STATES = ("Ready for Gmail draft", "In Gmail drafts", "Sent", "Skip")
FIELDS = [
    {"name": "Subject", "type": "singleLineText"},
    {"name": "To", "type": "email"},
    {"name": "First name", "type": "singleLineText"},
    {"name": "Company", "type": "singleLineText"},
    {"name": "Body", "type": "multilineText"},
    {"name": "Context", "type": "multilineText"},
    {"name": "Contact record", "type": "singleLineText"},
    {"name": "Step", "type": "singleSelect", "options": {"choices": [{"name": s} for s in ("Email 1", "Follow-up 1", "Follow-up 2")]}},
    {"name": "Status", "type": "singleSelect", "options": {"choices": [{"name": s} for s in STATES]}},
    {"name": "Note", "type": "multilineText"},
    {"name": "Sent at", "type": "dateTime", "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "utc"}},
    {"name": "Replied", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
]
Emails = at.Table("Emails")


def context(company, jobs):
    bits = [f"{k}: {company[k]}" for k in ("Funding stage", "Employees (Apollo)", "Total funding", "Website") if company.get(k)]
    posts = [j.get("Job URL") for j in jobs if j.get("Job URL")][:1]
    return "\n".join(bits + ([f"Hiring post: {posts[0]}"] if posts else []))


def eligible(contact, company):
    if not contact.get("Email") or contact.get("Email status") != "verified":
        return False
    if contact.get("Stage") not in (None, "", "New"):
        return False
    return bool(company and company.get("Shortlist") and company.get("Status") != "Excluded" and not ob.EXCLUDE.search(company.get("Company") or ""))


def auto_shortlist(companies, update=None):
    """Gate 1 for the one-run mode: tick Shortlist on companies that pass the wider early-stage ICP and have verified facts.
    Only ticks, never unticks. Drafts still wait in Gmail for Ayush to press Send."""
    import companies as co
    picked = []
    for c in companies:
        s = co.serialize(dict(c, jobs=[]))
        if not c.get("Shortlist") and c.get("Status") != "Excluded" and s["matched"] and co.icp_check(s, co.RELAXED_ICP)[0]:
            picked.append((c["_id"], {"Shortlist": True}))
            c["Shortlist"] = True
    if picked:
        (update or at.Companies.update)(picked)
    return len(picked)


def build(contacts=None, companies=None, jobs=None, emails=None, update_contacts=None, auto_shortlist_on=False, update_companies=None):
    contacts = contacts if contacts is not None else at.Contacts.all()
    companies_list = companies if companies is not None else at.Companies.all()
    if auto_shortlist_on:
        auto_shortlist(companies_list, update_companies)
    companies = {c["_id"]: c for c in companies_list}
    jobs = jobs if jobs is not None else at.Jobs.all()
    emails = emails or Emails
    have = {e.get("Contact record") for e in emails.all()} | {e.get("To", "").lower() for e in emails.all()}
    by_company = {}
    for j in jobs:
        for cid in j.get("Company LinkedIn URL") or []:
            by_company.setdefault(cid, []).append(j)
    rows, done = [], []
    for c in contacts:
        company = next((companies[i] for i in c.get("Company") or [] if i in companies), None)
        if not eligible(c, company) or c["_id"] in have or c["Email"].lower() in have:
            continue
        first = (c.get("First name") or "").split(" ")[0]
        if not first:
            continue
        built = ob.build(first, company.get("Company") or "", c["Email"].lower(), line=company.get("Hook") or None)
        rows.append({"Subject": built["subject"], "To": c["Email"].lower(), "First name": first, "Company": company.get("Company") or "",
                     "Body": built["body"], "Context": context(company, by_company.get(company["_id"], [])), "Contact record": c["_id"],
                     "Step": "Email 1", "Status": "Ready for Gmail draft"})
        done.append((c["_id"], {"Stage": "Drafted"}))
        have.add(c["_id"])
        have.add(c["Email"].lower())
    if rows:
        emails.create(rows)
        (update_contacts or at.Contacts.update)(done)
    return len(rows)


def setup():
    meta = f"https://api.airtable.com/v0/meta/bases/{base_id()}/tables"
    tables = {t["name"]: t for t in request("GET", meta, headers=_headers()).json()["tables"]}
    if "Emails" not in tables:
        request("POST", meta, headers=_headers(), json={"name": "Emails", "fields": FIELDS})
        print("created Emails table")
    else:  # additive: columns added after the first version
        have_e = {f["name"] for f in tables["Emails"]["fields"]}
        for f in FIELDS:
            if f["name"] not in have_e:
                request("POST", f"{meta}/{tables['Emails']['id']}/fields", headers=_headers(), json=f)
                print("added Emails." + f["name"])
    have = {f["name"] for f in tables["Companies"]["fields"]}
    if "Shortlist" not in have:
        request("POST", f"{meta}/{tables['Companies']['id']}/fields", headers=_headers(),
                json={"name": "Shortlist", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}})
        print("added Companies.Shortlist")


def stats():
    from collections import Counter
    print("EMAILS", dict(Counter(e.get("Status") for e in Emails.all())))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"setup": setup, "build": lambda: print("built", build(auto_shortlist_on="--auto" in sys.argv)), "stats": stats}.get(cmd, lambda: print(__doc__))()
