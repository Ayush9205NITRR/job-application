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


def check_verify_company(r):
    if not isinstance(r.get("same_company"), bool):
        return "same_company must be true or false"
    if r["same_company"] and r.get("website") and not _url(r["website"]):
        return "website must be a full http(s) url"
    return None


def check_pick_decision_makers(r):
    people = r.get("people")
    if not isinstance(people, list):
        return "people must be a list"
    for p in people:
        if not isinstance(p, dict) or not p.get("name") or p.get("role") not in ROLES:
            return f"each person needs name and role in {ROLES}"
        if p.get("linkedin") and not re.match(r"^https://([a-z]+\.)?linkedin\.com/in/", p["linkedin"]):
            return "linkedin must be a /in/ profile url"
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
    fields = {k: v for k, v in {"Website": r.get("website"), "Funding stage": r.get("stage")}.items() if v}
    if fields:
        tables["Companies"].update([(task["Company record"], fields)])
    return f"updated {', '.join(fields) or 'nothing'}"


def apply_pick_decision_makers(task, r, tables):
    rows = []
    for p in r["people"]:
        first, _, last = p["name"].partition(" ")
        row = {"First name": first, "Last name": last, "Title": p.get("title", ""), "Contact type": p["role"], "Stage": "New",
               "Company": [task["Company record"]]}
        if p.get("linkedin"):
            row["LinkedIn URL"] = p["linkedin"]
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
def create_task(kind, company, company_record, payload, tasks=None):
    """Idempotent: one live task per (type, company record)."""
    tasks = tasks or Tasks
    key = f"{kind}:{company_record}"
    live = [t for t in tasks.all() if t.get("Task ID") == key and t.get("Status") in ("Open", "Working", "Done", "Needs human")]
    if live:
        return None
    return tasks.create([{"Task ID": key, "Type": kind, "Company": company, "Company record": company_record,
                          "Input": json.dumps(payload, ensure_ascii=False), "Status": "Open", "Tries": 0, "Created at": stamp()}])[0]


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
            status, note = "Applied", APPLY[kind](t, r, tables)
        tasks.update([(t["_id"], {"Status": status, "Note": note})])
        out[status] += 1
    return out


def requeue_stale(tasks=None, at_time=None):
    tasks, now_ = tasks or Tasks, at_time or now()
    n = 0
    for t in tasks.all('{Status}="Working"'):
        started = datetime.fromisoformat((t.get("Started at") or stamp(now_ - STALE * 2)).replace("Z", "+00:00"))
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


def show():
    """Read-only: every task with its Result, and for the 5 known test companies whether the agent's answer is the right one."""
    right = total = 0
    for t in Tasks.all():
        res = t.get("Result") or ""
        print(f"- {t.get('Company')} | {t.get('Type')} | {t.get('Status')} | conf={t.get('Confidence')} | started={t.get('Started at')}")
        print(f"    result: {res[:300]!r}")
        key = next((k for k in EXPECTED if k in (t.get("Company") or "").lower()), None)
        if key and t.get("Type") == "classify_post":
            try:
                got = json.loads(res).get("kind")
            except (ValueError, AttributeError):
                got = None
            total += 1
            right += got == EXPECTED[key]
            print(f"    expected={EXPECTED[key]} got={got} -> {'OK' if got == EXPECTED[key] else 'WRONG'}")
            bad = CHECKS["classify_post"](json.loads(res)) if got else "unreadable"
            if bad:
                print(f"    code check would say: {bad}")
    print(f"MATCH {right}/{total}")


def reopen(names, tasks=None):
    """Put finished tasks back to Open (clears Result, Confidence, Started at) so the agent redoes them. Match by company name substring."""
    tasks, n = tasks or Tasks, 0
    for t in tasks.all():
        if t.get("Status") in ("Done", "Failed", "Needs human") and any(x.lower() in (t.get("Company") or "").lower() for x in names):
            tasks.update([(t["_id"], {"Status": "Open", "Result": "", "Confidence": None, "Started at": None, "Note": "reopened for a rerun"})])
            n += 1
    return n


def setup():
    meta = f"https://api.airtable.com/v0/meta/bases/{base_id()}/tables"
    have = {t["name"] for t in request("GET", meta, headers=_headers()).json()["tables"]}
    if "Tasks" in have:
        print("Tasks table already exists")
        return
    request("POST", meta, headers=_headers(), json={"name": "Tasks", "fields": FIELDS})
    print("created Tasks table")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"setup": setup, "reopen": lambda: print("reopened", reopen([x.strip() for x in sys.argv[2].split("|") if x.strip()])), "where": where, "show": show, "seed": lambda: print("seeded", seed_classify_post([x.strip() for x in sys.argv[2].split("|") if x.strip()] if len(sys.argv) > 2 else None)), "apply": lambda: print(apply_done()), "requeue": lambda: print("requeued", requeue_stale())}.get(cmd, lambda: print(__doc__))()
