#!/usr/bin/env python3
"""Stage 6: Gmail drafts for Contacts with Stage = New. Drafts only. Nothing is sent.

Guards: verified emails only, never Do not contact, only companies where the job is marked Applied,
daily cap with ramp, halts if the bounce rate is too high.
First line: uses the contact's optional "First line" field if filled (e.g. written by Claude via Airtable MCP),
otherwise generates one with Gemini. Lines that fail the style check are skipped, never sent generic.
"""
import argparse
from datetime import date, datetime, timezone

from fo import airtable as at
from fo import gmail, llm, rules, sync
from fo import templates as T
from fo.config import IST, env


def start_date(touches) -> date | None:
    s = env("SEND_START_DATE", required=False)
    if s:
        return date.fromisoformat(s)
    ds = [sync.parse_dt(t["Drafted at"]).astimezone(IST).date() for t in touches if t.get("Drafted at")]
    return min(ds) if ds else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="render and print, create no drafts, write nothing")
    ap.add_argument("--max", type=int, help="override remaining daily allowance (can only lower it)")
    args = ap.parse_args()
    at.DRY_RUN = args.dry_run

    touches = at.Touches.all()
    contacts_all = at.Contacts.all()
    sent = sum(1 for t in touches if t.get("Sent at") and t.get("Step") == 1)
    bounced = sum(1 for c in contacts_all if c.get("Stage") == "Bounced")
    if rules.bounce_halt(sent, bounced):
        raise SystemExit(f"STOP: {bounced} bounces from {sent} sent. Review before drafting more.")

    today = datetime.now(IST).date()
    used = sum(1 for t in touches if t.get("Drafted at") and sync.parse_dt(t["Drafted at"]).astimezone(IST).date() == today)
    allowance = rules.daily_cap(start_date(touches), today) - used
    if args.max is not None:
        allowance = min(allowance, args.max)
    print(f"daily allowance left: {allowance}")
    if allowance <= 0:
        return

    companies = {c["_id"]: c for c in at.Companies.all()}
    jobs = at.Jobs.all()
    applied_company_ids = {i for j in jobs if j.get("Applied") for i in (j.get("Company LinkedIn URL") or [])}
    for cid in applied_company_ids:  # Status sync: Enriched -> Applied
        if companies.get(cid, {}).get("Status") == "Enriched":
            at.Companies.update([(cid, {"Status": "Applied"})])

    new = [c for c in contacts_all if c.get("Stage") == "New" and c.get("Email status") == "verified"]
    new.sort(key=lambda c: c.get("Contact type") != "Founder")  # founders first
    resume = env("RESUME_URL", required=False)
    made = 0
    for c in new:
        if made >= allowance:
            break
        co = companies.get(at.first_link(c, "Company"))
        if not co:
            continue
        applied = [j for j in jobs if j.get("Applied") and co["_id"] in (j.get("Company LinkedIn URL") or [])]
        if not applied:
            print(f"skip {c['Email']}: no job marked Applied at {co.get('Company')}")
            continue
        job = applied[0]
        ctype = c.get("Contact type", "Founder")
        tpl = T.template_for(ctype)
        proof_id, subj_id = T.pick_proof(ctype), T.subject_id(tpl, c["Email"])
        v = {
            "firstName": c.get("First name"), "companyName": co.get("Company"), "roleTitle": job.get("Title"),
            "jobUrl": job.get("Job URL"), "startupType": co.get("Startup type") or "",
            "proofLine": T.PROOFS[proof_id],
        }
        if tpl == "A":
            line = (c.get("First line") or llm.first_line(co["Company"], co.get("About", ""), job["Title"])).strip()
            problem = rules.check_first_line(line)
            if problem:
                print(f"skip {c['Email']}: first line rejected ({problem}): {line!r}")
                continue
            v["firstLine"] = line
        try:
            subject = T.render(T.SUBJECTS[subj_id], v)
            body = T.render(T.BODIES[(tpl, 1)], v)
        except KeyError as e:
            print(f"skip {c['Email']}: {e}")
            continue
        if args.dry_run:
            print(f"--- {c['Email']} [{T.tag(tpl, subj_id, proof_id)}]\nSubject: {subject}\n\n{body}\n")
            made += 1
            continue
        _, thread_id = gmail.create_draft(c["Email"], subject, body)
        at.Touches.create([{
            "Contact": [c["_id"]], "Channel": "Email", "Template": T.tag(tpl, subj_id, proof_id), "Step": 1,
            "Subject": subject, "Body": body, "Drafted at": datetime.now(timezone.utc).isoformat(),
            "Gmail thread ID": thread_id,
        }])
        at.Contacts.update([(c["_id"], {"Stage": "Drafted"})])
        made += 1
        print(f"drafted {c['Email']}")
    print(f"{made} drafts created. Review in Gmail Drafts, send 9-11 AM IST.")


if __name__ == "__main__":
    main()
