"""Numbers for the dashboard, read-only. Prints one JSON object between markers (the workflow log carries it).
    python dashboard.py"""
import json
from collections import Counter
from datetime import datetime

import alerts
import tasks as T
from fo import airtable as at

STUCK_HOURS = 24


def _age_h(s, now_):
    try:
        return (now_ - datetime.fromisoformat(str(s).replace("Z", "+00:00"))).total_seconds() / 3600
    except ValueError:
        return 0


def build(companies, jobs, contacts, tasks, emails, your, run_rows, now_=None):
    now_ = now_ or T.now()
    tc = Counter((t.get("Type"), t.get("Status")) for t in tasks)
    oldest = {}
    for t in tasks:
        if t.get("Status") == "Open":
            oldest[t.get("Type")] = max(oldest.get(t.get("Type"), 0), _age_h(t.get("Created at"), now_))
    ec = Counter((e.get("Step"), e.get("Status")) for e in emails)
    with_li = [c for c in contacts if c.get("LinkedIn URL")]
    with_mail = [c for c in contacts if c.get("Email") and c.get("Email status") == "verified"]
    short = [c for c in companies if c.get("Shortlist") and c.get("Status") != "Excluded"]
    todo = alerts.open_rows_from(your)
    apply_open = [r for r in todo if str(r.get("Key", "")).startswith("apply_")]
    last = max((r.get("Run") or "" for r in run_rows), default="")
    latest = [r for r in run_rows if r.get("Run") == last]

    def stage(n, name, owner, done, waiting=0, wait_h=0, note=""):
        stuck = waiting > 0 and wait_h >= STUCK_HOURS
        return {"step": n, "stage": name, "owner": owner, "done": done, "waiting": waiting,
                "state": "stuck" if stuck else "waiting" if waiting else "ok", "note": note}

    ty = lambda k, s: tc[(k, s)]
    funnel = [
        stage(1, "Fetched from Apify (last run)", "Code", sum(int(r.get("Raw") or 0) for r in latest), note=f"run {last or 'none yet'}"),
        stage(2, "New unique companies (dedupe on LinkedIn URL)", "Code", sum(int(r.get("New") or 0) for r in latest),
              note=f"{sum(int(r.get('Already listed') or 0) for r in latest)} already listed, skipped"),
        stage(3, "Classified: startup or not", "Agent", ty("classify_post", "Applied"), ty("classify_post", "Open"), oldest.get("classify_post", 0)),
        stage(4, "ICP verified with facts", "Agent", ty("verify_company", "Applied"), ty("verify_company", "Open"), oldest.get("verify_company", 0)),
        stage(5, "Shortlisted accounts", "Code", len(short), note=f"{len(companies)} companies in total"),
        stage(6, "Apply now with your CV", "You", len([r for r in your if str(r.get('Key', '')).startswith('apply_') and r.get('Status') == 'Done']),
              len(apply_open), max((_age_h(r.get("Created at"), now_) for r in apply_open), default=0)),
        stage(7, "Decision makers found (LinkedIn)", "Agent", len(with_li), ty("pick_decision_makers", "Open"), oldest.get("pick_decision_makers", 0)),
        stage(8, "Emails added by you", "You", len(with_mail), len(with_li) - len([c for c in with_li if c.get("Email")])),
        stage(9, "Personalised email in Gmail drafts", "Agent", ec[("Email 1", "In Gmail drafts")] + ec[("Email 1", "Sent")],
              ec[("Email 1", "Ready for Gmail draft")]),
        stage(10, "Sent by you", "You", ec[("Email 1", "Sent")], ec[("Email 1", "In Gmail drafts")]),
        stage(11, "Follow-ups drafted or sent", "Agent", sum(v for (s, st), v in ec.items() if s != "Email 1" and st in ("In Gmail drafts", "Sent")),
              sum(v for (s, st), v in ec.items() if s != "Email 1" and st == "Ready for Gmail draft"),
              note=f"{sum(1 for e in emails if e.get('Replied'))} replied"),
    ]
    keywords = [{"run": r.get("Run"), "source": r.get("Source"), "keyword": r.get("Keyword"), "raw": int(r.get("Raw") or 0),
                 "kept": int(r.get("Kept") or 0), "unique": int(r.get("Unique companies") or 0), "new": int(r.get("New") or 0),
                 "already_listed": int(r.get("Already listed") or 0), "dropped": r.get("Dropped") or ""}
                for r in sorted(run_rows, key=lambda r: (r.get("Run") or "", r.get("Keyword") or ""), reverse=True)[:40]]
    people = Counter(i for c in with_li for i in c.get("Company") or [])
    mails = Counter(i for c in with_mail for i in c.get("Company") or [])
    accounts = [{"company": c.get("Company"), "stage": c.get("Funding stage") or "", "employees": c.get("Employees (Apollo)") or "",
                 "source": c.get("Source") or "", "hook": c.get("Hook") or "", "people": people[c["_id"]], "emails": mails[c["_id"]],
                 "next": "add emails" if people[c["_id"]] > mails[c["_id"]] else "find people" if not people[c["_id"]] else "drafts"}
                for c in short]
    todos = [{"kind": str(r.get("Key", "")).split("_")[0], "title": r.get("Title"), "steps": r.get("Steps") or "",
              "blocking": not str(r.get("Key", "")).startswith(alerts.INFO)} for r in todo]
    return {"updated": T.stamp(), "funnel": funnel, "keywords": keywords, "accounts": accounts, "todos": todos}


if __name__ == "__main__":
    out = build(at.Companies.all(), at.Jobs.all(), at.Contacts.all(), T.Tasks.all(), at.Table("Emails").all(),
                alerts.Table.all(), at.Table("Runs").all())
    print("DASHBOARD_JSON_BEGIN" + json.dumps(out, ensure_ascii=False) + "DASHBOARD_JSON_END")
