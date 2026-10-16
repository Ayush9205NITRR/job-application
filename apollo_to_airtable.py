#!/usr/bin/env python3
"""Join an Apollo People/Companies CSV export onto Airtable Companies and fill in funding and other details.

    python apollo_to_airtable.py apollo.csv --base appms9ERcB5Ws3r0w --dry-run
    python apollo_to_airtable.py apollo.csv --base appms9ERcB5Ws3r0w

Match order per Apollo company: (1) company LinkedIn slug, (2) website domain, (3) normalised company name.
Needs AIRTABLE_TOKEN in the environment (schema.bases:write too, it adds the new fields). --base defaults to
AIRTABLE_BASE_ID. Writes only the new "Apollo ..." / funding fields, never Status or ICP fields. The CSV is
read locally and never committed (*.csv is gitignored). A match report is written next to it.
"""
import argparse
import csv
import re
from pathlib import Path

from fo import airtable as at
from fo.http import request
from fo.rules import domain_of, norm_company_url

KEY = "Company LinkedIn URL"
# Airtable field -> (list of Apollo headers, normalised: lowercase, symbols stripped; field type)
FIELDS = {
    "Funding stage": (["latestfunding", "latestfundingstage"], "singleLineText"),
    "Latest funding amount": (["latestfundingamount"], "singleLineText"),
    "Total funding": (["totalfunding"], "singleLineText"),
    "Employees (Apollo)": (["employees", "numberofemployees", "companysize"], "singleLineText"),
    "Industry (Apollo)": (["industry"], "singleLineText"),
    "Founded (Apollo)": (["foundedyear"], "singleLineText"),
    "Annual revenue (Apollo)": (["annualrevenue"], "singleLineText"),
    "About (Apollo)": (["shortdescription", "seodescription", "companydescription"], "multilineText"),
    "Apollo match": ([], "singleLineText"),  # how it matched: linkedin / domain / name
}
COMPANY_COLS = {"name": ["companyname", "company", "organization", "organizationname"],
                "li": ["companylinkedinurl", "organizationlinkedinurl"], "site": ["website", "companywebsite"]}
_SUFFIX = re.compile(r"\b(private|pvt|limited|ltd|llp|inc|incorporated|llc|corp|corporation|co|technologies|technology)\b")


def nh(h):
    return re.sub(r"[^a-z0-9]", "", h.lower())


def norm_name(s):
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()))).strip()


def load_apollo(path):
    """-> {company key: {name, li, site, <airtable field>: value}}, first non-empty value wins per company."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {nh(k): (v or "").strip() for k, v in raw.items() if k}
            pick = lambda keys: next((r[k] for k in keys if r.get(k)), "")
            name, li, site = (pick(COMPANY_COLS[k]) for k in ("name", "li", "site"))
            key = norm_company_url(li) or domain_of(site) or norm_name(name)
            if not key:
                continue
            c = out.setdefault(key, {"name": name, "li": norm_company_url(li), "site": domain_of(site)})
            for field, (headers, _) in FIELDS.items():
                if headers and not c.get(field) and pick(headers):
                    c[field] = pick(headers)
    return out


def match(apollo, companies):
    """-> (matches [(airtable_row, apollo_company, method)], unmatched apollo companies)."""
    by_li = {norm_company_url(c.get(KEY)): c for c in companies if norm_company_url(c.get(KEY))}
    by_dom = {domain_of(c.get("Website")): c for c in companies if domain_of(c.get("Website"))}
    by_name = {}
    for c in companies:
        by_name.setdefault(norm_name(c.get("Company")), []).append(c)
    matches, unmatched = [], []
    for a in apollo.values():
        if a["li"] and a["li"] in by_li:
            matches.append((by_li[a["li"]], a, "linkedin"))
        elif a["site"] and a["site"] in by_dom:
            matches.append((by_dom[a["site"]], a, "domain"))
        elif len(by_name.get(norm_name(a["name"]), [])) == 1 and norm_name(a["name"]):  # ambiguous names never match
            matches.append((by_name[norm_name(a["name"])][0], a, "name"))
        else:
            unmatched.append(a)
    return matches, unmatched


def updates(matches):
    out = []
    for row, a, method in matches:
        f = {k: v for k, v in a.items() if k in FIELDS and v}
        f["Apollo match"] = method
        if not row.get("Website") and a["site"]:
            f["Website"] = "https://" + a["site"]  # also helps enrichment later
        out.append((row["_id"], f))
    return out


def ensure_fields(base):
    meta = f"https://api.airtable.com/v0/meta/bases/{base}/tables"
    tables = request("GET", meta, headers=at._headers()).json()["tables"]
    t = next((t for t in tables if t["name"] == "Companies"), None)
    if not t:
        raise SystemExit(f"base {base} has no Companies table")
    have = {f["name"] for f in t["fields"]}
    for name, (_, ftype) in FIELDS.items():
        if name not in have:
            print(f"adding field {name}")
            body = {"name": name, "type": ftype}
            if ftype == "checkbox":
                body["options"] = {"icon": "check", "color": "greenBright"}
            request("POST", f"{meta}/{t['id']}/fields", headers=at._headers(), json=body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--base", help="app... id or Airtable URL (default: AIRTABLE_BASE_ID)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    base = re.search(r"app[A-Za-z0-9]{14}", a.base).group(0) if a.base else at.base_id()

    apollo = load_apollo(a.csv)
    companies = at.Table("Companies", base=base).all()
    matches, unmatched = match(apollo, companies)
    by = {}
    for _, _, m in matches:
        by[m] = by.get(m, 0) + 1
    print(f"{len(apollo)} Apollo companies, {len(companies)} Airtable companies in {base}")
    print(f"matched {len(matches)} {by}; not in Airtable: {len(unmatched)}")

    report = Path(a.csv).with_suffix(".match.csv")
    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["apollo_company", "airtable_company", "method", "status", "funding_stage", "total_funding"])
        for row, ap_, m in matches:
            w.writerow([ap_["name"], row.get("Company"), m, row.get("Status"), ap_.get("Funding stage"), ap_.get("Total funding")])
        for ap_ in unmatched:
            w.writerow([ap_["name"], "", "NO MATCH", "", ap_.get("Funding stage"), ap_.get("Total funding")])
    print(f"report: {report}")
    if a.dry_run:
        return
    ensure_fields(base)
    at.Table("Companies", base=base).update(updates(matches))
    print(f"updated {len(matches)} companies")


if __name__ == "__main__":
    main()
