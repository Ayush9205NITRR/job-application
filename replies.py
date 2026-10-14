#!/usr/bin/env python3
"""Stage 9: poll Gmail threads of sent touches. Log replies, detect bounces, classify, draft a response.
Drafts only. Gemini classifies by default; any value can be overwritten by Claude via Airtable MCP."""
import argparse

from fo import airtable as at
from fo import gmail, llm, sync


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    at.DRY_RUN = args.dry_run

    if not args.dry_run:
        sync.sync_sent()
    me = gmail.my_address()
    contacts = {c["_id"]: c for c in at.Contacts.all()}
    companies = {c["_id"]: c for c in at.Companies.all()}
    jobs = at.Jobs.all()
    logged = {(at.first_link(r, "Contact"), sync.parse_dt(r["Received at"])) for r in at.Replies.all() if r.get("Received at")}
    threads: dict[str, dict] = {}
    for t in at.Touches.all():
        if t.get("Channel") == "Email" and t.get("Sent at") and t.get("Gmail thread ID"):
            threads.setdefault(t["Gmail thread ID"], t)

    new = 0
    for thread_id, t in threads.items():
        cid = at.first_link(t, "Contact")
        c = contacts.get(cid)
        msgs = gmail.thread_messages(thread_id)
        if not c or not msgs:
            continue
        for m in (x for x in msgs if not x["sent"] and me not in x["from"]):
            if gmail.is_bounce(m):
                if c.get("Stage") != "Bounced":
                    at.Contacts.update([(cid, {"Stage": "Bounced", "Email status": "invalid"})])
                    print(f"BOUNCE: {c['Email']} removed from sequence")
                    c["Stage"] = "Bounced"
                continue
            if (cid, m["at"]) in logged:
                continue
            received = m["at"].isoformat()
            co = companies.get(at.first_link(c, "Company"), {})
            job = next((j for j in jobs if co.get("_id") in (j.get("Company LinkedIn URL") or []) and j.get("Applied")), {})
            res = llm.classify_reply(m["body"], job.get("Title", "the role"), co.get("Company", ""))
            at.Replies.create([{"Contact": [cid], "Received at": received, "Body": m["body"][:5000],
                                "Class": res["class"], "Suggested response": res["suggested_response"]}])
            stage = "Do not contact" if res["class"] == "Unsubscribe" else "Replied"
            at.Contacts.update([(cid, {"Stage": stage})])
            if co and stage == "Replied":
                at.Companies.update([(co["_id"], {"Status": "Replied"})])
            if res["suggested_response"] and not args.dry_run:
                subj = t["Subject"] if t["Subject"].lower().startswith("re:") else f"Re: {t['Subject']}"
                gmail.create_draft(c["Email"], subj, res["suggested_response"], thread_id=thread_id, in_reply_to=m["message_id"])
            print(f"reply from {c['Email']}: {res['class']}")
            new += 1
    print(f"{new} new replies logged")


if __name__ == "__main__":
    main()
