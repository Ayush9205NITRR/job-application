#!/usr/bin/env python3
"""LinkedIn is manual. This prints today's queue with the exact text to paste, and logs what you sent.

    python linkedin.py                    # today's invite queue (max 15) + day-8 messages due
    python linkedin.py --log a@b.com invite     # after you sent the invite
    python linkedin.py --log a@b.com message    # after you sent the day-8 message
Invite = Touch step 1, message = step 4 (channel distinguishes them from the email sequence).
"""
import argparse
from datetime import datetime, timedelta, timezone

from fo import airtable as at
from fo import rules, sync
from fo import templates as T

MAX_INVITES = 15
INVITE_LIMIT = 300


def context(contacts, companies, jobs, touches):
    """Yield (contact, company, job, email_step1_touch) for contacts that are in the sequence."""
    for c in contacts:
        co = companies.get(at.first_link(c, "Company"))
        if not co or c.get("Stage") in rules.BLOCKED_STAGES or not c.get("LinkedIn URL"):
            continue
        job = next((j for j in jobs if j.get("Applied") and co["_id"] in (j.get("Company LinkedIn URL") or [])), None)
        step1 = next((t for t in touches if c["_id"] in (t.get("Contact") or []) and t.get("Channel") == "Email"
                      and t.get("Step") == 1), None)
        if job and step1:
            yield c, co, job, step1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", nargs=2, metavar=("EMAIL", "KIND"))
    args = ap.parse_args()
    contacts = at.Contacts.all()
    if args.log:
        email, kind = args.log[0].lower(), args.log[1]
        c = next((x for x in contacts if x.get("Email", "").lower() == email), None)
        if not c or kind not in ("invite", "message"):
            raise SystemExit("usage: --log <contact email> invite|message")
        at.Touches.create([{
            "Contact": [c["_id"]], "Channel": "LinkedIn invite" if kind == "invite" else "LinkedIn message",
            "Step": 1 if kind == "invite" else 4, "Subject": f"LinkedIn {kind}",
            "Sent at": datetime.now(timezone.utc).isoformat(),
        }])
        print("logged")
        return

    companies = {c["_id"]: c for c in at.Companies.all()}
    jobs, touches = at.Jobs.all(), at.Touches.all()
    done = {(cid, t["Channel"]) for t in touches for cid in t.get("Contact") or []}
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    sent_today = sum(1 for t in touches if t.get("Channel") == "LinkedIn invite" and (t.get("Sent at") or "").startswith(today))
    room = MAX_INVITES - sent_today

    invites, messages = [], []
    for c, co, job, step1 in context(contacts, companies, jobs, touches):
        v = {"firstName": c.get("First name"), "companyName": co.get("Company"), "roleTitle": job.get("Title"),
             "proofLine": T.PROOFS[T.pick_proof(c.get("Contact type", "Founder"))]}
        if (c["_id"], "LinkedIn invite") not in done:
            text = T.render(T.LINKEDIN_INVITE, v)
            if len(text) > INVITE_LIMIT:  # proof line too long: fall back to the shortest proof
                v["proofLine"] = min(T.PROOFS.values(), key=len).replace("{{companyName}}", co["Company"])
                text = T.render(T.LINKEDIN_INVITE, v)
            if len(text) <= INVITE_LIMIT:
                invites.append((c, text))
        elif (c["_id"], "LinkedIn message") not in done and step1.get("Sent at") \
                and now - sync.parse_dt(step1["Sent at"]) >= timedelta(days=8):
            v["secondProof"] = T.PROOFS[T.second_proof_id(T.parse_tag(step1["Template"])[2])]
            messages.append((c, T.render(T.LINKEDIN_MESSAGE, v)))

    print(f"== Invites ({min(room, len(invites))} of {room} left today) ==")
    for c, text in invites[: max(room, 0)]:
        print(f"\n{c.get('First name')} {c.get('Last name') or ''} | {c['LinkedIn URL']}\n{text}  [{len(text)} chars]"
              f"\n  after sending: python linkedin.py --log {c['Email']} invite")
    print(f"\n== Day-8 messages due ({len(messages)}) ==")
    for c, text in messages:
        print(f"\n{c.get('First name')} | {c['LinkedIn URL']}\n{text}\n  after sending: python linkedin.py --log {c['Email']} message")


if __name__ == "__main__":
    main()
