#!/usr/bin/env python3
"""Import a CSV of startups/founders into Grist (Companies + Founders) with a header mapping.

  python3 scripts/import_csv.py imports/list.csv --dry-run          # parse + show mapping + preview, no network
  python3 scripts/import_csv.py imports/list.csv                    # import
  python3 scripts/import_csv.py imports/list.csv --map config/csv_mapping.json

Mapping: auto-detected from header names (see ALIASES). Override / fix with a JSON file:
  {"Company": "Startup Name", "Email": "Work Email", "Designation": "Title"}
Fields: Company Website Vertical Stage Last_Round_Date Round_Amount Job_Link Name First Last LinkedIn
        Designation Email WhatsApp   (all optional except you need at least one person identifier)

One CSV row = one PERSON. Companies are created once (by lowercased name) and linked.
"""
import argparse
import csv
import datetime
import io
import json
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import formulas as F  # noqa: E402

ALIASES = {
    "Company": ["company", "company name", "startup", "startup name", "organization", "organisation", "account", "account name"],
    "Website": ["website", "company website", "domain", "url", "site"],
    "Vertical": ["vertical", "industry", "sector", "category"],
    "Stage": ["stage", "funding stage", "round", "funding round", "last round", "last funding type", "latest funding type"],
    "Last_Round_Date": ["last round date", "funding date", "announced date", "last funding date", "latest funding date", "date"],
    "Round_Amount": ["amount", "round size", "funding amount", "last funding amount"],
    "Job_Link": ["job link", "job url", "jd", "jd link", "job", "careers link", "role link", "job posting"],
    "Name": ["name", "full name", "person", "contact", "contact name", "founder", "founder name"],
    "First": ["first name", "firstname", "first"],
    "Last": ["last name", "lastname", "surname", "last"],
    "LinkedIn": ["linkedin", "linkedin url", "linkedin id", "linkedin profile", "person linkedin url", "profile url"],
    "Designation": ["designation", "title", "job title", "role", "position"],
    "Email": ["email", "e mail", "email id", "work email", "email address", "email 1"],
    "WhatsApp": ["whatsapp", "whatsapp number", "phone", "phone number", "mobile", "mobile number", "contact number"],
}

DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y", "%b %Y", "%B %Y"]


def norm_header(h):
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def detect_mapping(headers, override=None):
    mapping = {}
    by_norm = {norm_header(h): h for h in headers}
    for field, aliases in ALIASES.items():
        for a in aliases:
            if a in by_norm:
                mapping[field] = by_norm[a]
                break
    # "Name" must not steal the company column when there is no explicit person-name column
    if "Name" in mapping and "Company" not in mapping and norm_header(mapping["Name"]) == "name":
        mapping["Company"], mapping["Name"] = mapping["Name"], None
        mapping = {k: v for k, v in mapping.items() if v}
    if override:
        mapping.update({k: v for k, v in override.items() if v})
        for k, v in override.items():
            if not v:
                mapping.pop(k, None)
    return mapping


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def norm_designation(raw):
    t = (raw or "").lower()
    if re.search(r"co[\s\-]?founder", t):
        return "Co-Founder"
    if "founder" in t:
        return "Founder"
    if re.search(r"\bceo\b|chief executive", t):
        return "CEO"
    if re.search(r"\bcoo\b|chief operating", t):
        return "COO"
    return ""


def norm_vertical(raw):
    t = (raw or "").lower()
    if re.search(r"travel|tour|hospitality|trip|holiday", t):
        return "Travel"
    if re.search(r"ed[\s\-]?tech|education|learning|upskill|school|course", t):
        return "Edtech"
    return ""


def norm_stage(raw):
    t = (raw or "").lower()
    if "series a" in t:
        return "Series A"
    if "series b" in t:
        return "Series B"
    if "seed" in t:
        return "Seed"
    return ""


def domain_of(url):
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0]


def read_rows_text(text):
    text = text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return read_rows_text(f.read())


def build(rows, mapping):
    """Return (companies{key: fields}, people[list of dict with _ckey]), skipped list."""
    emailn = F.compile_formula(F.F_EMAIL_NORM, "Email")
    wan = F.compile_formula(F.F_WA_NORM, "WhatsApp")
    companies, people, skipped = {}, [], []
    g = lambda r, f: (r.get(mapping[f]) or "").strip() if f in mapping else ""  # noqa: E731
    for i, r in enumerate(rows, start=2):  # header is line 1
        cname = g(r, "Company")
        name = g(r, "Name") or (g(r, "First") + " " + g(r, "Last")).strip()
        email, wa, li = g(r, "Email"), g(r, "WhatsApp"), g(r, "LinkedIn")
        if not (name or email or wa or li):
            skipped.append((i, "no person identifier"))
            continue
        ckey = (cname or domain_of(g(r, "Website")) or "").lower().strip()
        if ckey and ckey not in companies:
            companies[ckey] = {
                "Company": cname or domain_of(g(r, "Website")),
                "Website": g(r, "Website"),
                "Vertical": norm_vertical(g(r, "Vertical")),
                "Stage": norm_stage(g(r, "Stage")),
                "Last_Round_Date": parse_date(g(r, "Last_Round_Date")),
                "Round_Amount": g(r, "Round_Amount"),
                "Job_Link": g(r, "Job_Link"),
                "Status": "New",
            }
        elif ckey and not companies[ckey].get("Job_Link") and g(r, "Job_Link"):
            companies[ckey]["Job_Link"] = g(r, "Job_Link")
        desig_raw = g(r, "Designation")
        desig = norm_designation(desig_raw)
        notes = f"title: {desig_raw}" if desig_raw and not desig else ""
        has_contact = bool(emailn(Email=email) or wan(WhatsApp=wa))
        people.append({
            "_ckey": ckey, "_line": i,
            "Name": name, "LinkedIn": li, "Designation": desig, "Email": email, "WhatsApp": wa,
            "Status": "Contact-ready" if has_contact else "New",
            "Notes": notes,
        })
    return companies, people, skipped


def analyse(rows, mapping):
    """Dry-run summary as a plain dict (used by the CLI and by the UI)."""
    companies, people, skipped = build(rows, mapping)
    emailn = F.compile_formula(F.F_EMAIL_NORM, "Email")
    wan = F.compile_formula(F.F_WA_NORM, "WhatsApp")
    lin = F.compile_formula(F.F_LI_NORM, "LinkedIn")
    seen, dup = {}, 0
    for p in people:
        keys = [k for k in (("e", emailn(Email=p["Email"])), ("w", wan(WhatsApp=p["WhatsApp"])),
                            ("l", lin(LinkedIn=p["LinkedIn"]))) if k[1]]
        if any(k in seen for k in keys):
            dup += 1
        for k in keys:
            seen.setdefault(k, p["_line"])
    return {
        "rows": len(rows), "companies": len(companies), "people": len(people), "skipped": len(skipped),
        "duplicates_in_file": dup,
        "designations": dict(Counter(p["Designation"] or "(other/blank)" for p in people)),
        "with_email": sum(1 for p in people if emailn(Email=p["Email"])),
        "with_whatsapp": sum(1 for p in people if wan(WhatsApp=p["WhatsApp"])),
        "with_linkedin": sum(1 for p in people if p["LinkedIn"]),
        "preview": [{k: v for k, v in p.items() if not k.startswith("_")} for p in people[:5]],
    }


def write_import(rows, mapping, source):
    """Write Companies + Founders to Grist. Returns a stats dict."""
    import grist_client as G
    companies, people, _ = build(rows, mapping)
    existing = {(r["fields"].get("Company") or "").lower().strip(): r["id"] for r in G.fetch("Companies")}
    new_keys = [k for k in companies if k not in existing]
    rows_new = []
    for k in new_keys:
        c = dict(companies[k])
        d = c.pop("Last_Round_Date")
        if d:
            c["Last_Round_Date"] = G.ts_date(d.isoformat())
        rows_new.append({kk: vv for kk, vv in c.items() if vv not in ("", None)})
    created = G.add("Companies", rows_new) if rows_new else []
    for k, rec in zip(new_keys, created):
        existing[k] = rec["id"]
    out = []
    for p in people:
        f = {k: v for k, v in p.items() if not k.startswith("_") and v}
        if p["_ckey"] in existing:
            f["Company"] = existing[p["_ckey"]]
        f["Source"] = source
        out.append(f)
    G.add("Founders", out)
    allf = G.fetch("Founders")
    return {"new_companies": len(new_keys), "founders_added": len(out), "founders_total": len(allf),
            "is_dup_total": sum(1 for r in allf if r["fields"].get("Is_Dup"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--map", help="JSON mapping override")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = read_rows(args.csv)
    if not rows:
        sys.exit("empty CSV")
    headers = list(rows[0].keys())
    override = json.loads(pathlib.Path(args.map).read_text()) if args.map else None
    mapping = detect_mapping(headers, override)

    print(f"rows: {len(rows)}   headers: {headers}")
    print("mapping:")
    for f in ALIASES:
        print(f"  {f:16} <- {mapping.get(f, '(none)')}")
    unmapped = [h for h in headers if h not in mapping.values()]
    if unmapped:
        print("unmapped columns (ignored):", unmapped)
    st = analyse(rows, mapping)
    print(f"\ncompanies: {st['companies']}  people: {st['people']}  skipped: {st['skipped']}  duplicates inside file: {st['duplicates_in_file']}")
    print("designations:", st["designations"])
    print("with email:", st["with_email"], " with whatsapp:", st["with_whatsapp"], " with linkedin:", st["with_linkedin"])
    for p in st["preview"][:3]:
        print("preview:", p)
    if args.dry_run:
        print("\n(dry run, nothing written)")
        return
    r = write_import(rows, mapping, "csv:" + pathlib.Path(args.csv).name)
    print(f"\nimported: {r['new_companies']} new companies, {r['founders_added']} founders. "
          f"Founders table now {r['founders_total']} rows, Is_Dup=true on {r['is_dup_total']}.")


if __name__ == "__main__":
    main()
