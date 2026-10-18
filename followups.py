"""Follow-ups from the Emails table. The agent records what happened in Gmail (Status Sent, Sent at, Replied);
this code then adds a Follow-up 1 row after 5 days and a Follow-up 2 row after 12 days, unless the person replied.
    python followups.py            add the rows that are due
The agent drafts them as replies in the same Gmail thread. You press Send."""
from datetime import datetime, timedelta

import outbound as ob
import tasks as T
from fo import airtable as at

STEPS = ("Email 1", "Follow-up 1", "Follow-up 2")
DUE = {"Follow-up 1": timedelta(days=ob.FU1_DAYS), "Follow-up 2": timedelta(days=ob.FU2_DAYS)}


def _when(r):
    return datetime.fromisoformat(r["Sent at"].replace("Z", "+00:00")) if r.get("Sent at") else None


def due(rows, now_):
    """-> list of (first_row, step) that need a new row. Pure."""
    by = {}
    for r in rows:
        by.setdefault(r.get("Contact record") or r.get("To", "").lower(), []).append(r)
    out = []
    for group in by.values():
        if any(r.get("Replied") or r.get("Status") == "Skip" for r in group):
            continue
        have = {r.get("Step") for r in group}
        first = next((r for r in group if r.get("Step") == "Email 1" and r.get("Status") == "Sent" and _when(r)), None)
        if not first:
            continue
        age = now_ - _when(first)
        fu1 = next((r for r in group if r.get("Step") == "Follow-up 1"), None)
        if "Follow-up 1" not in have and age >= DUE["Follow-up 1"]:
            out.append((first, "Follow-up 1"))
        elif fu1 and fu1.get("Status") == "Sent" and "Follow-up 2" not in have and age >= DUE["Follow-up 2"]:
            out.append((first, "Follow-up 2"))
    return out


def build(emails=None, now_=None):
    emails = emails or at.Table("Emails")
    todo = due(emails.all(), now_ or T.now())
    rows = []
    for first, step in todo:
        text = ob.build(first.get("First name") or "", first.get("Company") or "", first["To"])["fu1" if step == "Follow-up 1" else "fu2"]
        rows.append({"Subject": "Re: " + first.get("Subject", ""), "To": first["To"], "First name": first.get("First name"),
                     "Company": first.get("Company"), "Body": text, "Contact record": first.get("Contact record"), "Step": step,
                     "Status": "Ready for Gmail draft",
                     "Context": f"Make this a REPLY inside the sent thread '{first.get('Subject')}' (Gmail > Sent > open it > Reply). Not a new email."})
    if rows:
        emails.create(rows)
    return len(rows)


if __name__ == "__main__":
    print("follow-ups added", build())
