"""Company stage of the flow: Apollo company CSV -> Airtable, ICP highlight, shortlist, apply-calendar, POC import.

Needs in the environment: AIRTABLE_TOKEN, AIRTABLE_BASE_ID (the base that holds Companies and Jobs).
stdlib only at import time; `requests` is loaded lazily when Airtable is actually used.
"""
import csv
import hashlib
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import outbound as ob
import overlap

IST = timezone(timedelta(hours=5, minutes=30))
SEED = re.compile(r"seed|pre[- ]?seed|angel|bootstrap", re.I)
DONE = {"Applied", "Contacted", "Replied", "Closed", "Enriched"}   # never reset these by shortlisting


def airtable_ready():
    return bool(os.environ.get("AIRTABLE_TOKEN") and os.environ.get("AIRTABLE_BASE_ID"))


def employees(s):
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group().replace(",", "")) if m else None


def icp(c):
    """Seed / pre-seed, 2 to 50 employees (REQUIREMENTS section 2.1). -> (is_icp, reason)."""
    stage, emp = (c.get("Funding stage") or "").strip(), employees(c.get("Employees (Apollo)"))
    if not SEED.search(stage):
        return False, f"stage: {stage or 'unknown'}"
    if emp is not None and not 2 <= emp <= 50:
        return False, f"{emp} employees"
    return True, "seed" + (f", {emp} employees" if emp else "")


# ---- Airtable --------------------------------------------------------------
def fetch():
    """All Companies with their Jobs attached under c['jobs']."""
    from fo import airtable as at
    comps, jobs = at.Companies.all(), at.Jobs.all()
    by = {}
    for j in jobs:
        for cid in j.get("Company LinkedIn URL") or []:
            by.setdefault(cid, []).append(j)
    for c in comps:
        c["jobs"] = by.get(c["_id"], [])
    return comps


def import_apollo_companies(path):
    """Match an Apollo CSV onto Airtable Companies and write funding / employees / industry. -> summary dict."""
    from fo import airtable as at
    import apollo_to_airtable as a
    a.FIELDS.setdefault("Shortlist", ([], "checkbox"))
    apollo = a.load_apollo(path)
    comps = at.Companies.all()
    matches, unmatched = a.match(apollo, comps)
    a.ensure_fields(at.base_id())
    at.Companies.update(a.updates(matches))
    methods = {}
    for _, _, m in matches:
        methods[m] = methods.get(m, 0) + 1
    return {"apollo": len(apollo), "matched": len(matches), "unmatched": len(unmatched), "methods": methods}


def save_shortlist(selected, shown):
    """Tick = Shortlist true and Status Shortlisted. Only rows in `shown` are touched."""
    from fo import airtable as at
    selected, shown = set(selected), set(shown)
    updates = []
    for c in at.Companies.all():
        if c["_id"] not in shown:
            continue
        want, f = c["_id"] in selected, {}
        if bool(c.get("Shortlist")) != want:
            f["Shortlist"] = want
        if want and c.get("Status") not in DONE and c.get("Status") != "Shortlisted":
            f["Status"] = "Shortlisted"
        if not want and c.get("Status") == "Shortlisted":
            f["Status"] = "Needs review"
        if f:
            updates.append((c["_id"], f))
    at.Companies.update(updates)
    return len(updates)


def mark_applied(company_ids):
    from fo import airtable as at
    ids = set(company_ids)
    at.Companies.update([(i, {"Status": "Applied"}) for i in ids])
    at.Jobs.update([(j["_id"], {"Applied": True}) for j in at.Jobs.all() if ids & set(j.get("Company LinkedIn URL") or [])])
    return len(ids)


# ---- Calendar: one .ics, low effort -------------------------------------------
def schedule(comps, per_block=5, hour=11, minute=0, length=30, today=None):
    """Shortlisted companies that have a job and are not applied yet, in blocks of `per_block`, one block per workday."""
    todo = [c for c in comps if c.get("Shortlist") and c.get("Status") not in DONE and c.get("jobs")]
    todo.sort(key=lambda c: c.get("Company") or "")
    day = (today or datetime.now(IST).date()) + timedelta(days=1)
    blocks = []
    for i in range(0, len(todo), max(per_block, 1)):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        items = []
        for c in todo[i:i + per_block]:
            j = c["jobs"][0]
            items.append((c.get("Company") or "?", j.get("Title") or "job", j.get("Job URL") or ""))
        blocks.append({"start": datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST), "length": length, "items": items})
        day += timedelta(days=1)
    return blocks


def _esc(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while cut > 0 and (b[cut] & 0xC0) == 0x80:  # do not split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b[cut:]
        b = b" " + b
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def summary(block):
    return "Apply to %d Founder's Office jobs" % len(block["items"])


def make_ics(blocks, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//outbound//apply blocks//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for b in blocks:
        names = "|".join(i[0] for i in b["items"])
        uid = hashlib.sha1((b["start"].date().isoformat() + names).encode()).hexdigest() + "@outbound"  # stable: re-import updates, no duplicates
        body = [f"{n}. {c} - {t} - {u}" for n, (c, t, u) in enumerate(b["items"], 1)] + ["", "Apply, then tick 'Mark applied' in the UI."]
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{fmt(now)}", f"DTSTART:{fmt(b['start'])}",
                  f"DTEND:{fmt(b['start'] + timedelta(minutes=b['length']))}",
                  f"SUMMARY:{_esc(summary(b))}",
                  f"DESCRIPTION:{_esc(chr(10).join(body))}", "BEGIN:VALARM", "ACTION:DISPLAY",
                  "DESCRIPTION:Apply block starts in 10 minutes", "TRIGGER:-PT10M", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# ---- POC CSV -> queue rows -------------------------------------------------------
POC_ALIASES = {
    "li": ["companylinkedinprofile", "companylinkedinurl", "companylinkedin", "companyurl", "linkedin"],
    "first": ["pocfirstname", "firstname", "first"], "last": ["poclastname", "lastname"],
    "phone": ["poccontactno", "poccontactnumber", "pocphone", "contactno", "contactnumber", "phone", "mobilephone"],
    "email": ["pocemail", "email", "emailaddress"], "status": ["emailstatus", "pocemailstatus"], "title": ["poctitle", "title"],
}


def import_pocs(path, verified, comps, proof, cache_dir, fetch=None, log=print):
    """Add POCs of SHORTLISTED companies to queue.csv as drafts (never approved). -> (added, skipped dict).
    `verified`: True/False answer to 'are these emails verified?' when the CSV has no Email Status column."""
    from fo.rules import norm_company_url
    fetch = fetch or overlap.default_fetch
    by_li = {norm_company_url(c.get("Company LinkedIn URL")): c for c in comps if c.get("Shortlist")}
    rows = ob.load_queue()
    seen = {r["email"].lower() for r in rows}
    skipped = {"company not shortlisted": 0, "unverified email": 0, "duplicate": 0, "excluded": 0, "missing name/email": 0}
    texts, added = {}, 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {ob._norm(k): (v or "").strip() for k, v in raw.items() if k}
            pick = lambda k: next((r[a] for a in POC_ALIASES[k] if r.get(a)), "")
            em, first, li = pick("email").lower(), pick("first"), norm_company_url(pick("li"))
            if not em or not first:
                skipped["missing name/email"] += 1
                continue
            co = by_li.get(li)
            if not co:
                skipped["company not shortlisted"] += 1
                continue
            status = pick("status")
            if (status.lower() != "verified") if status else (not verified):
                skipped["unverified email"] += 1
                continue
            if em in seen:
                skipped["duplicate"] += 1
                continue
            name = co.get("Company") or ""
            if ob.EXCLUDE.search(f"{name} | {co.get('Industry (Apollo)', '')}"):
                skipped["excluded"] += 1
                continue
            site = co.get("Website") or ""
            if site and site not in texts:
                log(f"reading {site} for overlap")
                texts[site] = overlap.site_text(site, cache_dir, fetch)
            ov = overlap.find_overlap(name, co.get("Industry (Apollo)", ""), texts.get(site, ""), proof)
            phone = ob.clean_phone(pick("phone"))
            row = {c: "" for c in ob.COLUMNS}
            row.update(email=em, first=first, company=name, title=pick("title"), phone=phone,
                       about=re.sub(r"\s+", " ", co.get("About (Apollo)") or co.get("About") or "")[:300], company_li=li or "",
                       overlap_note=ov["note"], suggested_line=ov["line"] if ov["level"] == "Medium" else "",
                       **ob.build(first, name, em, phone, line=ov["line"] if ov["level"] == "High" else None))
            rows.append(row)
            seen.add(em)
            added += 1
            ob.save_queue(rows)
    log(f"pocs: added {added}, skipped {skipped}")
    return added, skipped
