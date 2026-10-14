#!/usr/bin/env python3
"""Stage 4: Apollo enrichment for Status = Shortlisted companies only.

For each: org enrich on the website domain, people search for founder-level titles, keep VERIFIED emails only,
add the job poster as a second contact, write Contacts, set company Status = Enriched.
"""
import argparse

from fo import airtable as at
from fo import apollo, rules
from fo.config import MAX_CONTACTS_PER_COMPANY


def contact_row(person: dict, company_id: str, ctype: str) -> dict | None:
    if person.get("email_status") != "verified" or not person.get("email"):
        return None
    return {
        "Email": person["email"].lower(), "First name": person.get("first_name"), "Last name": person.get("last_name"),
        "Title": person.get("title"), "LinkedIn URL": person.get("linkedin_url"), "Company": [company_id],
        "Email status": "verified", "Contact type": ctype, "Stage": "New",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=15, help="max companies per run (protects Apollo free credits)")
    args = ap.parse_args()
    at.DRY_RUN = args.dry_run

    companies = at.Companies.all('{Status}="Shortlisted"')[: args.limit]
    jobs = at.Jobs.all()
    existing = {c["Email"].lower() for c in at.Contacts.all() if c.get("Email")}
    print(f"{len(companies)} shortlisted companies to enrich")

    for co in companies:
        domain = rules.domain_of(co.get("Website"))
        if not domain:
            print(f"skip {co.get('Company')}: no website")
            continue
        rows = []
        try:
            org = apollo.enrich_org(domain)
            domain = rules.domain_of(org.get("primary_domain") or org.get("website_url")) or domain
            for p in apollo.search_people(domain)[:MAX_CONTACTS_PER_COMPANY]:
                full = apollo.match_person(person_id=p.get("id"), domain=domain)
                r = full and contact_row(full, co["_id"], "Founder")
                if r:
                    rows.append(r)
            # Job poster as a second contact.
            for j in jobs:
                if co["_id"] in (j.get("Company LinkedIn URL") or []) and j.get("Poster LinkedIn URL"):
                    full = apollo.match_person(linkedin_url=j["Poster LinkedIn URL"])
                    r = full and contact_row(full, co["_id"], "Job poster")
                    if r:
                        rows.append(r)
                    break
        except RuntimeError as e:
            print(f"apollo error on {co.get('Company')}: {e}")
            continue
        seen, fresh = set(existing), []
        for r in rows:
            if r["Email"] not in seen:
                seen.add(r["Email"])
                fresh.append({k: v for k, v in r.items() if v})
        if fresh:
            at.Contacts.upsert(fresh, ["Email"])
            existing |= {r["Email"] for r in fresh}
            at.Companies.update([(co["_id"], {"Status": "Enriched"})])
        print(f"{co.get('Company')}: {len(fresh)} verified contacts" + ("" if fresh else " (company stays Shortlisted)"))


if __name__ == "__main__":
    main()
