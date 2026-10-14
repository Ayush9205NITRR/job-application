#!/usr/bin/env python3
"""Stage 8, run daily. Syncs what Ayush sent, then drafts day-5 and day-12 follow-ups in the same thread.
Stops on any reply, bounce, or Do not contact. Drafts only."""
import argparse
from datetime import datetime, timezone

from fo import airtable as at
from fo import gmail, rules, sync
from fo import templates as T
from fo.config import IST, env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    at.DRY_RUN = args.dry_run

    if not args.dry_run:
        print(f"{sync.sync_sent()} touches newly marked as sent")
    touches = at.Touches.all()
    contacts = {c["_id"]: c for c in at.Contacts.all()}
    companies = {c["_id"]: c for c in at.Companies.all()}
    replied = {i for r in at.Replies.all() for i in (r.get("Contact") or [])}
    jobs = at.Jobs.all()
    resume = env("RESUME_URL", required=False)
    now = datetime.now(timezone.utc)

    # Respect the same daily cap as step 1 (follow-ups are emails too).
    from draft import start_date

    today = datetime.now(IST).date()
    used = sum(1 for t in touches if t.get("Drafted at") and sync.parse_dt(t["Drafted at"]).astimezone(IST).date() == today)
    allowance = rules.daily_cap(start_date(touches), today) - used

    by_contact: dict[str, list[dict]] = {}
    for t in touches:
        if t.get("Channel") == "Email":
            for cid in t.get("Contact") or []:
                by_contact.setdefault(cid, []).append(t)

    made = 0
    for cid, ts in by_contact.items():
        c = contacts.get(cid)
        step1 = next((t for t in ts if t.get("Step") == 1 and t.get("Sent at")), None)
        if not c or not step1 or cid in replied or c.get("Stage") in rules.BLOCKED_STAGES:
            continue
        due = rules.followup_step_due(sync.parse_dt(step1["Sent at"]), now, {t["Step"] for t in ts})
        if not due:
            continue
        if made >= allowance:
            print("daily cap reached, remaining follow-ups wait for tomorrow")
            break
        co = companies.get(at.first_link(c, "Company"), {})
        tpl, _, proof = T.parse_tag(step1["Template"])
        job = next((j for j in jobs if co.get("_id") in (j.get("Company LinkedIn URL") or []) and j.get("Applied")), {})
        v = {"firstName": c.get("First name"), "companyName": co.get("Company"), "roleTitle": job.get("Title"),
             "secondProof": T.PROOFS[T.second_proof_id(proof)], "resumeUrl": resume}
        msgs = None if args.dry_run else gmail.thread_messages(step1["Gmail thread ID"])
        if not args.dry_run and not msgs:
            continue
        try:
            body = T.render(T.BODIES[(tpl, due)], v)
        except KeyError as e:
            print(f"skip {c['Email']} step {due}: {e}")
            continue
        subject = step1["Subject"] if step1["Subject"].lower().startswith("re:") else f"Re: {step1['Subject']}"
        if args.dry_run:
            print(f"--- {c['Email']} step {due}\nSubject: {subject}\n\n{body}\n")
            made += 1
            continue
        if any(not m["sent"] for m in msgs):  # a non-sent-by-me message means a reply exists
            continue
        _, thread_id = gmail.create_draft(c["Email"], subject, body, thread_id=step1["Gmail thread ID"],
                                          in_reply_to=msgs[-1]["message_id"])
        at.Touches.create([{
            "Contact": [cid], "Channel": "Email", "Template": step1["Template"], "Step": due, "Subject": subject,
            "Body": body, "Drafted at": now.isoformat(), "Gmail thread ID": thread_id,
        }])
        made += 1
        print(f"drafted step {due} for {c['Email']}")
    print(f"{made} follow-up drafts")


if __name__ == "__main__":
    main()
