"""Reconcile Touches with Gmail: which drafts did Ayush actually send?"""
from datetime import datetime

from . import airtable as at
from . import gmail


def sync_sent() -> int:
    """Mark Touches as sent when the Nth SENT message in the thread exists (N = Step).
    Updates Contact Stage and Company Status. Returns number of touches newly marked."""
    touches = [t for t in at.Touches.all() if t.get("Channel") == "Email" and t.get("Gmail thread ID") and not t.get("Sent at")]
    if not touches:
        return 0
    contacts = {c["_id"]: c for c in at.Contacts.all()}
    companies = {c["_id"]: c for c in at.Companies.all()}
    n = 0
    by_thread: dict[str, list[dict]] = {}
    for t in touches:
        by_thread.setdefault(t["Gmail thread ID"], []).append(t)
    for thread_id, ts in by_thread.items():
        msgs = gmail.thread_messages(thread_id)
        if msgs is None:
            continue  # draft deleted by Ayush; left as-is
        sent = [m for m in msgs if m["sent"]]
        for t in ts:
            step = int(t.get("Step", 1))
            if len(sent) < step:
                continue
            at.Touches.update([(t["_id"], {"Sent at": sent[step - 1]["at"].isoformat()})])
            cid = at.first_link(t, "Contact")
            contact = contacts.get(cid)
            if contact and contact.get("Stage") not in ("Replied", "Bounced", "Do not contact"):
                at.Contacts.update([(cid, {"Stage": "Sent" if step == 1 else "Followed up"})])
            if contact:
                comp = at.first_link(contact, "Company")
                if comp and companies.get(comp, {}).get("Status") in ("Enriched", "Applied"):
                    at.Companies.update([(comp, {"Status": "Contacted"})])
            n += 1
    return n


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
