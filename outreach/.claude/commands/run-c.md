---
description: Sync Gmail, then create Gmail DRAFTS for approved rows (never sends)
argument-hint: [BATCH_SIZE=25]
---

RUN C: SYNC, THEN DRAFT. Arguments: $ARGUMENTS. Draft only; sending is blocked by settings and by me.

PART 1: SYNC (always first)
- `python3 scripts/reverse_map.py --apply` (read-only Gmail API + any `imports/views*.csv`: maps Sent -> Delivered -> Viewed -> Replied / Bounced back onto each person; sets Stage, Sent_At, Replied_At, Thread_ID, Status).
- Log notable changes (new replies, bounces) in `Outreach_Log`. If a reply says stop/unsubscribe: Reply = "Unsubscribed", Status = "Skip".

PART 2: EMAIL 1 drafts
Founders where Approve = true AND Status = "Ready" AND Is_Dup = false AND Email_norm non-empty AND Planned_Send <= end of today (IST). Max BATCH_SIZE (25).
1. DEDUPE GATE: `python3 scripts/gmail_sync.py --gate <Email_norm>`. If sent > 0 or drafts > 0 or anywhere_from_me > 0 -> Status = "Skip", Notes = "already contacted / draft exists", log Action = Skipped, move on.
2. HTML: `python3 scripts/build_email_html.py <row_id> --which 1` (turns {{RESUME_LINK}} into my RESUME_URL link; no tracking pixels).
3. Create a Gmail DRAFT with the Gmail connector's create_draft tool (To = Email, Subject = Email_Subject, HTML body from step 2). If the tool can't take HTML or attachments, tell me which one and add "CV NOT ATTACHED" / "PLAIN TEXT ONLY" to Check_Before_Sending; the RESUME_URL link in the body covers the CV.
4. Verify the draft exists (list drafts / get draft). Then Status = "Drafted-1"; log Action = Drafted with the draft id.

PART 3: EMAIL 2 drafts
Founders with Status = "Sent-1", Reply empty/None, Sent2_Due <= today. Same dedupe logic for "already replied". Create the draft IN THE SAME THREAD (Re: original subject) with `--which 2` HTML. Status = "Drafted-2".

I send / schedule the drafts myself in Gmail. Next /run-c syncs them to Sent-1 / Sent-2.

WhatsApp and LinkedIn: not automated. I use the `WA_Link` buttons and `LI_Note` from the Grist Review page and tick WA_Sent / LI_Sent.

End with: drafts created (founder, company, planned send), skipped (why), replies/bounces found, and every Check_Before_Sending item I must look at.
