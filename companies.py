"""Company side of the flow: Apollo account CSV -> Airtable, numeric ICP, overlap with my clients, apply-calendar, contacts import.

Needs in the environment: AIRTABLE_TOKEN, AIRTABLE_BASE_ID (the base that holds Companies and Jobs).
stdlib only at import time; `requests` is loaded lazily when Airtable is actually used.
"""
import csv
import hashlib
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import outbound as ob
import overlap

IST = timezone(timedelta(hours=5, minutes=30))
SEED = re.compile(r"seed|pre[- ]?seed|angel|bootstrap", re.I)
DONE = {"Applied", "Contacted", "Replied", "Closed", "Enriched"}   # never reset these by shortlisting


def airtable_ready():
    return bool(os.environ.get("AIRTABLE_TOKEN") and os.environ.get("AIRTABLE_BASE_ID"))


_UNITS = {"k": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
          "l": 1e5, "lac": 1e5, "lakh": 1e5, "cr": 1e7, "crore": 1e7}


def parse_money(s):
    """'$2.5M' -> 2500000, '1,200,000' -> 1200000, '5 Cr' -> 5e7. Ranges give the lower bound. None if no number."""
    t = re.sub(r"(?i)usd|inr|rs\.?|[$\u20b9,]", "", str(s or "")).strip().lower()
    m = re.match(r"(\d+(?:\.\d+)?)\s*([a-z]+)?", t)
    if not m:
        return None
    return float(m.group(1)) * _UNITS.get(m.group(2) or "", 1)


def employees(s):
    n = parse_money(s)
    return int(n) if n is not None else None


def icp(c):
    """Kept for the CLI path: seed / pre-seed, 2 to 50 employees. -> (is_icp, reason)."""
    stage, emp = (c.get("Funding stage") or "").strip(), employees(c.get("Employees (Apollo)"))
    if not SEED.search(stage):
        return False, f"stage: {stage or 'unknown'}"
    if emp is not None and not 2 <= emp <= 50:
        return False, f"{emp} employees"
    return True, "seed" + (f", {emp} employees" if emp else "")


def serialize(c, inter=None):
    """Compact JSON for the UI: the 4 Apollo parameters as text and as numbers (for live filtering)."""
    funding, revenue, emp = c.get("Total funding") or "", c.get("Annual revenue (Apollo)") or "", c.get("Employees (Apollo)") or ""
    return {"id": c["_id"], "name": c.get("Company") or "", "li": c.get("Company LinkedIn URL") or "", "website": c.get("Website") or "",
            "status": c.get("Status") or "", "shortlist": bool(c.get("Shortlist")), "done": c.get("Status") in DONE,
            "matched": bool(c.get("Apollo match")), "stage": (c.get("Funding stage") or "").strip(),
            "funding": funding, "funding_n": parse_money(funding), "revenue": revenue, "revenue_n": parse_money(revenue),
            "employees": emp, "employees_n": employees(emp),
            "jobs": [{"title": j.get("Title") or "job", "url": j.get("Job URL") or ""} for j in c.get("jobs", [])],
            "inter": (inter or {}).get(c["_id"])}


_cache = {"rows": None}
_cache_lock = threading.Lock()


def get(refresh=False):
    """Companies with jobs, cached in memory so the UI does not wait on Airtable for every click."""
    with _cache_lock:
        if refresh or _cache["rows"] is None:
            _cache["rows"] = fetch()
        return _cache["rows"]


def invalidate():
    with _cache_lock:
        _cache["rows"] = None


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


def preview_apollo(path, comps=None):
    """Match an Apollo CSV onto the Airtable companies WITHOUT writing anything. Shows what would happen:
    match counts per method, which of the 4 parameters were found, ICP count, a sample of matches and misses."""
    import apollo_to_airtable as a
    comps = comps if comps is not None else get()
    with open(path, newline="", encoding="utf-8-sig") as f:
        csv_rows = sum(1 for _ in csv.DictReader(f))
    apollo = a.load_apollo(path)
    matches, unmatched = a.match(apollo, comps)
    methods, rows = {}, []
    for row, ap, method in matches:
        methods[method] = methods.get(method, 0) + 1
        ok, why = icp({"Funding stage": ap.get("Funding stage", ""), "Employees (Apollo)": ap.get("Employees (Apollo)", "")})
        rows.append({"apollo": ap["name"], "company": row.get("Company") or "", "method": method, "stage": ap.get("Funding stage", ""),
                     "funding": ap.get("Total funding", ""), "revenue": ap.get("Annual revenue (Apollo)", ""),
                     "employees": ap.get("Employees (Apollo)", ""), "icp": ok, "why": why})
    fields = {k: sum(1 for ap in apollo.values() if ap.get(k)) for k in ("Funding stage", "Total funding", "Annual revenue (Apollo)", "Employees (Apollo)")}
    return {"csv_rows": csv_rows, "apollo": len(apollo), "matched": len(matches), "unmatched": len(unmatched), "methods": methods,
            "icp": sum(1 for r in rows if r["icp"]), "fields_found": fields, "airtable_companies": len(comps),
            "rows": sorted(rows, key=lambda r: (not r["icp"], r["company"]))[:60], "unmatched_sample": [u["name"] or u.get("li") or "?" for u in unmatched[:15]]}


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
    with _cache_lock:  # keep the cache in step so the next screen does not refetch
        for c in _cache["rows"] or []:
            if c["_id"] in shown:
                c["Shortlist"] = c["_id"] in selected
                if c["_id"] in selected and c.get("Status") not in DONE:
                    c["Status"] = "Shortlisted"
                elif c["_id"] not in selected and c.get("Status") == "Shortlisted":
                    c["Status"] = "Needs review"
    return len(updates)


def mark_applied(company_ids):
    from fo import airtable as at
    ids = set(company_ids)
    at.Companies.update([(i, {"Status": "Applied"}) for i in ids])
    at.Jobs.update([(j["_id"], {"Applied": True}) for j in at.Jobs.all() if ids & set(j.get("Company LinkedIn URL") or [])])
    invalidate()
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


# ---- Overlap with my clients (parallel, so 50 companies do not take 50 x 10 seconds) ---------------------
def find_intersections(comps, clients, cache_dir, fetch=None, workers=8, progress=None, now=None):
    """-> {company_id: {names, pick, note, at}}. `pick` starts as all matches; the user can untick names."""
    from concurrent.futures import ThreadPoolExecutor
    fetch = fetch or overlap.default_fetch
    now = (now or datetime.now(timezone.utc)).isoformat()

    def one(c):
        site = c.get("Website") or ""
        if not site:
            return c["_id"], {"names": [], "pick": [], "note": "no website known", "at": now}
        text = overlap.site_text(site, cache_dir, fetch)
        names = overlap.match_clients(text, clients)
        note = ("matches: " + ", ".join(names)) if names else ("no overlap found" if text else "site not readable")
        return c["_id"], {"names": names, "pick": list(names), "note": note, "at": now}

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, r in ex.map(one, comps):
            out[cid] = r
            if progress:
                progress(len(out), len(comps))
    return out


# ---- Contacts CSV -> queue rows ---------------------------------------------------------------------------
POC_ALIASES = {
    "li": ["companylinkedin", "companylinkedinprofile", "companylinkedinurl", "companyurl", "linkedin"],
    "first": ["pocfirstname", "pocnamefirstname", "pocname", "firstname", "first"], "last": ["poclastname", "lastname"],
    "phone": ["poccontactdetail", "poccontactdetails", "poccontactno", "poccontactnumber", "pocphone", "contactdetail",
              "contactdetails", "contactno", "contactnumber", "phone", "mobilephone"],
    "email": ["pocemail", "email", "emailaddress"], "status": ["emailstatus", "pocemailstatus"], "title": ["poctitle", "title"],
}


def import_pocs(path, verified, comps, pitch=None, log=print):
    """Contacts of the SELECTED companies (comps with Shortlist true) -> queue.csv drafts, never approved.
    pitch: {company_id: [client names to mention]}. No names -> the generic email.
    `verified`: answer to 'are these emails verified?' when the CSV has no Email Status column.
    -> (added, skipped dict, pitched count)"""
    from fo.rules import norm_company_url
    pitch = pitch or {}
    by_li = {norm_company_url(c.get("Company LinkedIn URL")): c for c in comps if c.get("Shortlist")}
    rows = ob.load_queue()
    seen = {r["email"].lower() for r in rows}
    skipped = {"company not selected": 0, "unverified email": 0, "duplicate": 0, "excluded": 0, "missing name/email": 0}
    added = pitched = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {ob._norm(k): (v or "").strip() for k, v in raw.items() if k}
            pick = lambda k: next((r[a] for a in POC_ALIASES[k] if r.get(a)), "")
            em, first, li = pick("email").lower(), pick("first").split(" ")[0] if pick("first") else "", norm_company_url(pick("li"))
            if not em or not first:
                skipped["missing name/email"] += 1
                continue
            co = by_li.get(li)
            if not co:
                skipped["company not selected"] += 1
                continue
            status = pick("status")
            if (status.lower() != "verified") if status else (not verified):
                skipped["unverified email"] += 1
                continue
            if em in seen:
                skipped["duplicate"] += 1
                continue
            name = co.get("Company") or ""
            if ob.EXCLUDE.search(name):
                skipped["excluded"] += 1
                continue
            names = pitch.get(co["_id"]) or []
            phone = ob.clean_phone(pick("phone"))
            row = {c: "" for c in ob.COLUMNS}
            row.update(email=em, first=first, company=name, title=pick("title"), phone=phone,
                       about=re.sub(r"\s+", " ", co.get("About") or "")[:300], company_li=li or "",
                       pitch="; ".join(names), overlap_note=("pitch: " + ", ".join(names)) if names else "generic",
                       **ob.build(first, name, em, phone, line=overlap.pitch_line(name, names) if names else None))
            rows.append(row)
            seen.add(em)
            added += 1
            pitched += bool(names)
    ob.save_queue(rows)
    log(f"contacts: added {added} ({pitched} pitch, {added - pitched} generic), skipped {skipped}")
    return added, skipped, pitched
