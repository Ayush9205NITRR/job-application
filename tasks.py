"""Code <-> agent mailbox. Code writes Open tasks into the Airtable Tasks table, the agent fills Result, code validates and applies.
The agent touches only this table. Nothing here sends email.

    python tasks.py setup      create the Tasks table if missing (additive)
    python tasks.py seed ["Name1|Name2"]  open classify_post tasks for post-sourced companies
    python tasks.py apply      validate Done tasks and apply them (Applied / Failed / Needs human)
    python tasks.py requeue    reopen Working tasks stuck for 30+ minutes (max 2 retries)
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from fo import airtable as at
from fo.airtable import _headers, base_id
from fo.http import request

TYPES = ("verify_company", "pick_decision_makers", "classify_post")
STATUSES = ("Open", "Working", "Done", "Applied", "Failed", "Needs human")
ROLES = ("Founder", "HR", "TA")
STALE = timedelta(minutes=30)
MAX_TRIES = 2

FIELDS = [
    {"name": "Task ID", "type": "singleLineText"},
    {"name": "Type", "type": "singleSelect", "options": {"choices": [{"name": t} for t in TYPES]}},
    {"name": "Company", "type": "singleLineText"},
    {"name": "Company record", "type": "singleLineText"},
    {"name": "Input", "type": "multilineText"},
    {"name": "Result", "type": "multilineText"},
    {"name": "Confidence", "type": "singleSelect", "options": {"choices": [{"name": "High"}, {"name": "Low"}]}},
    {"name": "Status", "type": "singleSelect", "options": {"choices": [{"name": s} for s in STATUSES]}},
    {"name": "Tries", "type": "number", "options": {"precision": 0}},
    {"name": "Lane", "type": "number", "options": {"precision": 0}},
    {"name": "Audit", "type": "singleSelect", "options": {"choices": [{"name": "OK"}, {"name": "Wrong"}]}},
    {"name": "Note", "type": "multilineText"},
    {"name": "Created at", "type": "dateTime", "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "utc"}},
    {"name": "Started at", "type": "dateTime", "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "utc"}},
]
Tasks = at.Table("Tasks")


def now():
    return datetime.now(timezone.utc)


def stamp(t=None):
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---- Result checks: the agent's answer is data to verify, never trusted as is -----------------
def _url(v):
    return isinstance(v, str) and re.match(r"^https?://[^\s]+$", v) is not None


EXTRA = ("stage", "total_funding", "revenue", "employees")  # optional company facts the agent found (Apollo or the web)


def check_verify_company(r):
    if not isinstance(r.get("same_company"), bool):
        return "same_company must be true or false"
    if r["same_company"] and r.get("website") and not _url(r["website"]):
        return "website must be a full http(s) url"
    for k in EXTRA:
        if r.get(k) is not None and not isinstance(r[k], (str, int, float)):
            return f"{k} must be text or a number"
    return None


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_STATUS = ("verified", "risky", "invalid", "unknown")


def check_pick_decision_makers(r):
    people = r.get("people")
    if not isinstance(people, list):
        return "people must be a list"
    for p in people:
        if not isinstance(p, dict) or not p.get("name") or p.get("role") not in ROLES:
            return f"each person needs name and role in {ROLES}"
        if p.get("linkedin") and not re.match(r"^https://([a-z]+\.)?linkedin\.com/in/", p["linkedin"]):
            return "linkedin must be a /in/ profile url"
        if p.get("email") and not EMAIL.match(p["email"]):
            return f"email looks wrong: {p['email']}"
        if p.get("email_status") not in (None, *EMAIL_STATUS):
            return f"email_status must be one of {EMAIL_STATUS}"
    return None


KINDS = ("startup", "recruiter", "services_firm", "other")


def check_classify_post(r):
    if r.get("kind") not in KINDS:
        return f"kind must be one of {KINDS}"
    if not isinstance(r.get("reason", ""), str):
        return "reason must be text"
    return None


CHECKS = {"verify_company": check_verify_company, "pick_decision_makers": check_pick_decision_makers, "classify_post": check_classify_post}


# ---- What applying a good result does ---------------------------------------------------------
def apply_verify_company(task, r, tables):
    if not r["same_company"]:
        tables["Companies"].update([(task["Company record"], {"Status": "Needs review"})])
        return "not the same company, left for review"
    facts = {"Website": r.get("website"), "Funding stage": r.get("stage"), "Total funding": r.get("total_funding"),
             "Annual revenue (Apollo)": r.get("revenue"), "Employees (Apollo)": r.get("employees")}
    fields = {k: str(v) for k, v in facts.items() if v not in (None, "")}
    if not fields:
        return "updated nothing"
    fields["Apollo match"] = "agent"  # same meaning as the CSV match: this company now has verified facts
    try:
        tables["Companies"].update([(task["Company record"], fields)])
    except RuntimeError as e:
        if "UNKNOWN_FIELD_NAME" not in str(e):
            raise
        # Columns missing in this base (run `tasks.py setup` to add them): keep what fits, say what was skipped.
        keep = {k: v for k, v in fields.items() if k == "Website"}
        if keep:
            tables["Companies"].update([(task["Company record"], keep)])
        return f"updated {', '.join(keep) or 'nothing'}; {', '.join(set(fields) - set(keep))} not saved (columns missing, run setup)"
    return f"updated {', '.join(k for k in fields if k != 'Apollo match')}"


def apply_pick_decision_makers(task, r, tables):
    rows = []
    for p in r["people"]:
        first, _, last = p["name"].partition(" ")
        row = {"First name": first, "Last name": last, "Title": p.get("title", ""), "Contact type": p["role"], "Stage": "New",
               "Company": [task["Company record"]]}
        if p.get("linkedin"):
            row["LinkedIn URL"] = p["linkedin"]
        if p.get("email"):
            row["Email"] = p["email"].strip().lower()
            row["Email status"] = p.get("email_status") or "unknown"
        rows.append(row)
    if rows:
        tables["Contacts"].create(rows)
    return f"added {len(rows)} contacts"


def apply_classify_post(task, r, tables):
    status = {"startup": "Needs review", "recruiter": "Excluded", "services_firm": "Excluded", "other": "Excluded"}[r["kind"]]
    tables["Companies"].update([(task["Company record"], {"Status": status})])
    return f"post is {r['kind']}, company set to {status}" + (f" ({r['reason'][:120]})" if r.get("reason") else "")


APPLY = {"verify_company": apply_verify_company, "pick_decision_makers": apply_pick_decision_makers, "classify_post": apply_classify_post}


# ---- The loop -----------------------------------------------------------------------------------
def create_task(kind, company, company_record, payload, tasks=None, lane=None):
    """Idempotent: one task per (type, company record) ever. Reruns go through reopen(), never a second row."""
    tasks = tasks or Tasks
    key = f"{kind}:{company_record}"
    if any(t.get("Task ID") == key for t in tasks.all()):
        return None
    row = {"Task ID": key, "Type": kind, "Company": company, "Company record": company_record,
           "Input": json.dumps(payload, ensure_ascii=False), "Status": "Open", "Tries": 0, "Created at": stamp()}
    if lane:
        row["Lane"] = lane
    return tasks.create([row])[0]


def apply_done(tasks=None, tables=None):
    tasks, tables = tasks or Tasks, tables or {"Companies": at.Companies, "Contacts": at.Contacts}
    out = {"Applied": 0, "Failed": 0, "Needs human": 0}
    for t in tasks.all('{Status}="Done"'):
        kind = t.get("Type")
        try:
            r = json.loads(t.get("Result") or "")
            if not isinstance(r, dict):
                raise ValueError("Result must be a JSON object")
            bad = CHECKS[kind](r)
        except (ValueError, KeyError) as e:
            bad = f"unreadable result: {e}"
        if bad:
            status, note = "Failed", bad
        elif t.get("Confidence") != "High":
            status, note = "Needs human", "agent was not sure, please look"
        else:
            try:
                status, note = "Applied", APPLY[kind](t, r, tables)
            except RuntimeError as e:  # one bad task must not stop the rest
                status, note = "Failed", f"could not apply: {str(e)[:200]}"
        tasks.update([(t["_id"], {"Status": status, "Note": note})])
        out[status] += 1
    return out


def requeue_stale(tasks=None, at_time=None):
    tasks, now_ = tasks or Tasks, at_time or now()
    n = 0
    for t in tasks.all('{Status}="Working"'):
        started = datetime.fromisoformat((t.get("Started at") or stamp(now_ - STALE * 2)).replace("Z", "+00:00"))
        if started > now_ + timedelta(minutes=5):  # agent typed IST (UTC+5:30) into a UTC field
            started -= timedelta(hours=5, minutes=30)
        if now_ - started < STALE:
            continue
        tries = int(t.get("Tries") or 0) + 1
        tasks.update([(t["_id"], {"Status": "Failed" if tries > MAX_TRIES else "Open", "Tries": tries,
                                  "Note": "stuck too long" if tries > MAX_TRIES else "reopened, was stuck"})])
        n += 1
    return n


def seed_classify_post(names=None, n=5, tasks=None, companies=None, jobs=None):
    """Open classify_post tasks for companies that came from LinkedIn posts. names: substrings to pick (default: first n)."""
    tasks, companies, jobs = tasks or Tasks, companies or at.Companies, jobs or at.Jobs
    comps = {c["_id"]: c for c in companies.all()}
    made = []
    for j in sorted(jobs.all(), key=lambda j: j.get("Title", "")):
        if not str(j.get("Job ID", "")).startswith("post-"):
            continue
        for cid in j.get("Company LinkedIn URL") or []:
            c = comps.get(cid, {})
            name = c.get("Company") or ""
            if names and not any(x.lower() in name.lower() for x in names):
                continue
            payload = {"company": name, "linkedin": c.get("Company LinkedIn URL", ""), "post": j.get("Title", "")[7:], "post_url": j.get("Job URL", "")}
            if len(made) < (len(names) if names else n) and create_task("classify_post", name, cid, payload, tasks):
                made.append(name)
    return made


def where():
    """Which base the secret points at (no secret printed): known ids are named, anything else is only counted."""
    b = base_id()
    known = {"appms9ERcB5Ws3r0w": "NEW working base (Job Tracker update)", "app40Dtij4xm0M2aW": "OLD base"}
    meta = f"https://api.airtable.com/v0/meta/bases/{b}/tables"
    names = [t["name"] for t in request("GET", meta, headers=_headers()).json()["tables"]]
    print("base:", known.get(b, "UNKNOWN base"), "| tables:", names)
    for n in ("Companies", "Jobs", "Tasks"):
        if n in names:
            print(f"  {n}: {len(at.Table(n).all())} rows")


EXPECTED = {"nextdimension": "startup", "taskforge": "startup", "sabihr": "recruiter", "aelum": "services_firm", "desklee": "other"}


def review(t, now_=None):
    """Quality checks for one finished task -> (list of problems, parsed result or None). Pure, no I/O."""
    now_, problems, res = now_ or now(), [], None
    try:
        res = json.loads(t.get("Result") or "")
        if not isinstance(res, dict):
            raise ValueError("not an object")
    except ValueError:
        return ["Result is not valid JSON"], None
    bad = CHECKS[t["Type"]](res) if t.get("Type") in CHECKS else "unknown type"
    if bad:
        problems.append(f"fails code check: {bad}")
    if len(str(res.get("reason") or "").strip()) < 15:
        problems.append("no real reason written")
    if not t.get("Confidence"):
        problems.append("Confidence empty")
    started = t.get("Started at")
    if not started:
        problems.append("Started at empty")
    else:
        try:
            when = datetime.fromisoformat(started.replace("Z", "+00:00"))
            if when > now_ + timedelta(minutes=5):
                problems.append(f"Started at is in the future ({started}), probably IST not UTC")
        except ValueError:
            problems.append("Started at unreadable")
    if t.get("Type") == "verify_company" and res.get("same_company") is True and t.get("Confidence") == "High" and not res.get("website"):
        problems.append("High confidence but no website given")
    return problems, res


def show():
    """Read-only: every finished task with its Result and quality problems; the 5 known test companies also get a right/wrong verdict."""
    right = total = clean = n = 0
    for t in Tasks.all():
        if t.get("Status") not in ("Done", "Applied", "Needs human", "Failed"):
            print(f"- {t.get('Company')} | {t.get('Type')} | {t.get('Status')}  (not finished)")
            continue
        n += 1
        problems, res = review(t)
        clean += not problems
        print(f"- {t.get('Company')} | {t.get('Type')} | {t.get('Status')} | conf={t.get('Confidence')} | started={t.get('Started at')}")
        print(f"    result: {(t.get('Result') or '')[:300]!r}")
        print(f"    problems: {'; '.join(problems) or 'none'}")
        key = next((k for k in EXPECTED if k in (t.get("Company") or "").lower()), None)
        if key and t.get("Type") == "classify_post" and res:
            total += 1
            right += res.get("kind") == EXPECTED[key]
            print(f"    expected={EXPECTED[key]} got={res.get('kind')} -> {'OK' if res.get('kind') == EXPECTED[key] else 'WRONG'}")
    print(f"CLEAN {clean}/{n} finished tasks have no format problems; KNOWN-ANSWER MATCH {right}/{total}")


def readback():
    """Read-only: each task's Status and Note next to what its company looks like in Airtable now."""
    comps = {c["_id"]: c for c in at.Companies.all()}
    for t in sorted(Tasks.all(), key=lambda t: t.get("Company") or ""):
        c = comps.get(t.get("Company record"), {})
        print(f"- {t.get('Company')} | {t.get('Type')} | task={t.get('Status')} | company Status={c.get('Status')} | Website={c.get('Website') or '-'} | Funding stage={c.get('Funding stage', '(no column)')}")
        if t.get("Note"):
            print(f"    note: {t['Note'][:200]}")


def seed_dm(names, tasks=None, companies=None):
    """Open pick_decision_makers tasks for the named companies (used to test the type before the planner would create it)."""
    tasks, companies = tasks or Tasks, companies or at.Companies
    made = []
    for c in companies.all():
        name = c.get("Company") or ""
        if any(x.lower() in name.lower() for x in names):
            payload = {"company": name, "linkedin": c.get("Company LinkedIn URL", ""), "website": c.get("Website", ""), "people": []}
            if create_task("pick_decision_makers", name, c["_id"], payload, tasks):
                made.append(name)
    return made


def reopen(names, tasks=None):
    """Put finished (even Applied) tasks back to Open (clears Result, Confidence, Started at) so the agent redoes them.
    Each name is a company substring, optionally `substring@type` to reopen only that task type."""
    tasks, n = tasks or Tasks, 0
    wanted = [(x.split("@")[0].lower(), (x.split("@") + [""])[1]) for x in names]
    for t in tasks.all():
        if t.get("Status") not in ("Done", "Applied", "Failed", "Needs human"):
            continue
        if any(sub in (t.get("Company") or "").lower() and (not typ or typ == t.get("Type")) for sub, typ in wanted):
            tasks.update([(t["_id"], {"Status": "Open", "Result": "", "Confidence": None, "Started at": None, "Note": "reopened for a rerun"})])
            n += 1
    return n


def setup():
    """Additive: creates Tasks and Contacts if missing, adds missing fields to Tasks. Never edits existing fields."""
    meta = f"https://api.airtable.com/v0/meta/bases/{base_id()}/tables"
    tables = {t["name"]: t for t in request("GET", meta, headers=_headers()).json()["tables"]}
    if "Tasks" not in tables:
        request("POST", meta, headers=_headers(), json={"name": "Tasks", "fields": FIELDS})
        print("created Tasks table")
    else:
        have = {f["name"] for f in tables["Tasks"]["fields"]}
        for f in FIELDS:
            if f["name"] not in have:
                request("POST", f"{meta}/{tables['Tasks']['id']}/fields", headers=_headers(), json=f)
                print("added field Tasks." + f["name"])
    for table, wanted in (("Companies", COMPANY_FIELDS), ("Contacts", CONTACT_FIELDS)):
        if table in tables:
            have = {f["name"] for f in tables[table]["fields"]}
            for f in wanted:
                if f["name"] not in have:
                    request("POST", f"{meta}/{tables[table]['id']}/fields", headers=_headers(), json=f)
                    print(f"added field {table}.{f['name']}")
    if "Contacts" not in tables:
        made = request("POST", meta, headers=_headers(), json={"name": "Contacts", "fields": CONTACT_FIELDS}).json()
        request("POST", f"{meta}/{made['id']}/fields", headers=_headers(), json={
            "name": "Company", "type": "multipleRecordLinks", "options": {"linkedTableId": tables["Companies"]["id"]}})
        print("created Contacts table")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"setup": setup, "seed_dm": lambda: print("seeded", seed_dm([x.strip() for x in sys.argv[2].split("|") if x.strip()])), "readback": readback, "reopen": lambda: print("reopened", reopen([x.strip() for x in sys.argv[2].split("|") if x.strip()])), "where": where, "show": show, "seed": lambda: print("seeded", seed_classify_post([x.strip() for x in sys.argv[2].split("|") if x.strip()] if len(sys.argv) > 2 else None)), "apply": lambda: print(apply_done()), "requeue": lambda: print("requeued", requeue_stale())}.get(cmd, lambda: print(__doc__))()
