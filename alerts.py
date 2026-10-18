"""Things only Ayush can do, in one Airtable table ("Your tasks"). Code writes a row the moment it hits a wall.
    python alerts.py setup    create the table (additive)
    python alerts.py scan     add rows for waiting agent / drafts, close the ones that cleared itself
    python alerts.py list     print open rows
    python alerts.py check    list, and exit 1 only if a real wall is open (token, limit, wrong setup): GitHub then emails you
Rows closed by you: set Status = Done in Airtable."""
import re
import sys
from contextlib import contextmanager
from datetime import timedelta

import tasks as T
from fo import airtable as at
from fo.airtable import _headers, base_id
from fo.http import request

Table = at.Table("Your tasks")
AGENT_WAIT = timedelta(hours=2)

FIELDS = [
    {"name": "Key", "type": "singleLineText"},
    {"name": "Title", "type": "singleLineText"},
    {"name": "Steps", "type": "multilineText"},
    {"name": "Status", "type": "singleSelect", "options": {"choices": [{"name": "Open"}, {"name": "Done"}]}},
    {"name": "Created at", "type": "dateTime", "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "utc"}},
    {"name": "Mailed", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
]
CV = "https://tinyurl.com/at-nitrr-resume"

APIFY = ("apify_token", "Replace the Apify token (credits or limit used up)",
         "1. console.apify.com > Settings > API & Integrations: copy a token from an account with credit.\n"
         "2. GitHub repo > Settings > Secrets > Actions: update APIFY_TOKEN (and Codespaces secrets if used).\n"
         "3. Set this row to Done, then run Posts push again.")
AIRTABLE = ("airtable_full", "Airtable base is full (record or API limit)",
            "1. Open the base > Extensions > Usage, see which limit was hit.\n"
            "2. Delete old Jobs rows or move old data to a new base; or upgrade the plan.\n"
            "3. If you use a new base: change AIRTABLE_BASE_ID in GitHub secrets and in the workflow files.\n"
            "4. Set this row to Done.")


def classify_error(text):
    """Map an error message to an alert (key, title, steps), or None when it is not a wall we know."""
    t = text or ""
    if "api.apify.com" in t and re.search(r"-> (401|402|403):|usage|limit|credit|not-enough", t, re.I):
        return APIFY
    if "api.airtable.com" in t and re.search(r"BILLING_LIMIT|RECORD_LIMIT|BASE_LIMIT|exceed|-> 402:", t, re.I):
        return AIRTABLE
    return None


def raise_alert(key, title, steps, table=None, once=False, rows=None):
    """Idempotent: one Open row per key (once=True: one row ever, so a Done row is not reopened). Never raises."""
    table = table or Table
    try:
        if any(r.get("Key") == key and (once or r.get("Status") != "Done") for r in (rows if rows is not None else table.all())):
            return False
        table.create([{"Key": key, "Title": title, "Steps": steps, "Status": "Open", "Created at": T.stamp()}])
        return True
    except Exception as e:  # noqa: BLE001
        print(f"could not write alert {key}: {e}")
        return False


@contextmanager
def guard(table=None):
    """Wrap a run: a known wall becomes a row in 'Your tasks', then the error still goes up."""
    try:
        yield
    except (RuntimeError, SystemExit) as e:
        hit = classify_error(str(e))
        if hit:
            raise_alert(*hit, table=table)
            print(f"ALERT for you: {hit[1]}")
        raise


def scan(tasks=None, emails=None, table=None, now_=None):
    """State-based alerts. They open when the wait is real and close themselves when it clears."""
    tasks, table, now_ = tasks or T.Tasks, table or Table, now_ or T.now()
    emails = emails if emails is not None else at.Table("Emails")
    old = [t for t in tasks.all() if t.get("Status") == "Open" and t.get("Created at")
           and now_ - T.datetime.fromisoformat(t["Created at"].replace("Z", "+00:00")) > AGENT_WAIT]
    drafts = [e for e in emails.all() if e.get("Status") == "In Gmail drafts"]
    want = {}
    if old:
        want["agent_waiting"] = (f"Start the agent: {len(old)} tasks have waited over 2 hours",
                                 "Open the browser agent with AGENT.md. Nothing else is needed.")
    if drafts:
        want["drafts_waiting"] = (f"{len(drafts)} drafts are in Gmail: read and press Send",
                                  "Gmail > Drafts. Read each one, press Send on the ones you like. Delete the rest.")
    rows = table.all()
    for k, (title, steps) in want.items():
        raise_alert(k, title, steps, table)
    for r in rows:
        if r.get("Key") in ("agent_waiting", "drafts_waiting") and r.get("Status") != "Done" and r["Key"] not in want:
            table.update([(r["_id"], {"Status": "Done"})])
    return sorted(want)


INFO = ("agent_waiting", "drafts_waiting", "apply_", "emails_")  # your to-dos, not a broken run: shown, never fail the workflow


def blocking(rows):
    return [r for r in rows if not str(r.get("Key") or "").startswith(INFO)]


def notify(companies, jobs, contacts, table=None):
    """Your to-dos from the data: apply now (shortlisted company with a job or post link), and find emails
    (decision makers found with LinkedIn but no email). Apply rows are made once; email rows close themselves."""
    table = table or Table
    rows = table.all()
    jobs_of, people_of, made = {}, {}, []
    for j in jobs:
        for cid in j.get("Company LinkedIn URL") or []:
            jobs_of.setdefault(cid, []).append(j)
    for p in contacts:
        for cid in p.get("Company") or []:
            people_of.setdefault(cid, []).append(p)
    for c in companies:
        cid, name = c["_id"], c.get("Company") or "?"
        if c.get("Status") == "Excluded":
            continue
        links = [j["Job URL"] for j in jobs_of.get(cid, []) if j.get("Job URL")][:3]
        if c.get("Shortlist") and links:
            facts = ", ".join(str(c[k]) for k in ("Funding stage", "Employees (Apollo)") if c.get(k))
            steps = "\n".join([f"{i}. Open {u}" for i, u in enumerate(links, 1)] + [f"Apply with your CV: {CV}", "Set this row to Done when applied."])
            if raise_alert(f"apply_{cid}", f"Apply now: {name}" + (f" ({facts})" if facts else ""), steps, table, once=True, rows=rows):
                made.append(f"apply_{cid}")
        need = [p for p in people_of.get(cid, []) if p.get("LinkedIn URL") and not p.get("Email")]
        key = f"emails_{cid}"
        if need:
            steps = "\n".join(f"- {p.get('First name', '')} {p.get('Last name', '')}, {p.get('Title') or p.get('Contact type') or ''}: {p['LinkedIn URL']}" for p in need)
            steps += "\nPaste each email in Contacts > Email and set Email status = verified. The personalised email is then written for you."
            if raise_alert(key, f"Find emails: {name}, {len(need)} people", steps, table, rows=rows):
                made.append(key)
        else:
            for r in rows:
                if r.get("Key") == key and r.get("Status") != "Done":
                    table.update([(r["_id"], {"Status": "Done"})])
    return made


def open_rows(table=None):
    return open_rows_from((table or Table).all())


def open_rows_from(rows):
    return [r for r in rows if r.get("Status") != "Done"]


def show(table=None):
    rows = open_rows(table)
    print(f"YOUR TASKS: {len(rows)} open")
    for r in rows:
        print(f"- {r.get('Title')}\n{r.get('Steps')}\n")
    return rows


def setup():
    meta = f"https://api.airtable.com/v0/meta/bases/{base_id()}/tables"
    tables = {t["name"]: t for t in request("GET", meta, headers=_headers()).json()["tables"]}
    if "Your tasks" not in tables:
        request("POST", meta, headers=_headers(), json={"name": "Your tasks", "fields": FIELDS})
        print("created Your tasks table")
        return
    have = {f["name"] for f in tables["Your tasks"]["fields"]}
    for f in FIELDS:
        if f["name"] not in have:
            request("POST", f"{meta}/{tables['Your tasks']['id']}/fields", headers=_headers(), json=f)
            print("added Your tasks." + f["name"])


def digest(table=None, send=None):
    """Email you the new rows (needs GMAIL_USER + GMAIL_APP_PASSWORD secrets; without them the agent's report carries them)."""
    import os
    table = table or Table
    if not send and not (os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD")):
        print("digest: no Gmail secrets, skipped (the agent report lists these)")
        return 0
    new = [r for r in open_rows(table) if not r.get("Mailed")]
    if not new:
        return 0
    body = "\n\n".join(f"{r.get('Title')}\n{r.get('Steps')}" for r in new) + "\n\nAll of it: Airtable > Your tasks."
    if send is None:
        import outbound
        send = outbound.send_self
    send(f"[Outbound] {len(new)} things for you", body)
    table.update([(r["_id"], {"Mailed": True}) for r in new])
    return len(new)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "scan":
        print("open:", scan())
        print("new to-dos:", notify(at.Companies.all(), at.Jobs.all(), at.Contacts.all()))
    elif cmd == "digest":
        print("mailed", digest())
    elif cmd == "list":
        show()
    elif cmd == "check":
        scan()
        notify(at.Companies.all(), at.Jobs.all(), at.Contacts.all())
        rows = show()
        sys.exit(1 if blocking(rows) else 0)  # only a real wall (token, limit, wrong setup) turns the run red
    else:
        print(__doc__)
