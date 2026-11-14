---
description: Personalise run — research each founder and write email 1+2, WhatsApp 1+2, LinkedIn note
argument-hint: [BATCH_SIZE=10]
---

RUN B: PERSONALISE. Arguments: $ARGUMENTS

Pick Founders with Status = "Contact-ready" AND Is_Dup = false (max BATCH_SIZE, default 10). Read `resume_facts.md` fully and the `Templates` table (keys: email_1, email_2, wa_1, wa_2, li_note). If every Templates row is empty, stop and tell me.

For each founder (Company row gives Company, Vertical, Stage, Job_Link, Role_Target):

1. RESEARCH (web search): what the company does, who it sells to, its one distinctive product or bet; ONE recent verifiable fact (funding, launch, milestone) with date + source. If it only appears in a press release / contributor piece, say so. Optionally one recent public post by the founder, only if verified. -> `Research_Hook` (2 lines), `Sources` (URLs).
2. INTERSECTION: from resume_facts.md pick the 2-3 proofs matching THEIR biggest problem or the target role; skip the rest. One line "Their problem is X; my proof is Y." -> first line of `Check_Before_Sending` as `INTERSECTION: ...`.
3. PERSONALITY LINE: ONE light human line (a touch of humour) built on a TRUE fact about me or their product. No jokes about them, no puns, no forced cleverness, nothing egoistic. If it feels forced, drop it.
4. WRITE using my Templates rows as the skeleton: fill {{first_name}}, {{company}}, {{hook}}, {{proof}}, {{role}}; don't rewrite my template wording beyond that. If a template row is empty fall back to: hook -> why me in 3-5 bullets with numbers -> what I'm looking for -> one easy ask.
   - With Job_Link: name the role and reference the posting. Without: say I'm looking for a Founder's Office / Strategy & Ops / Growth role and ask if one is open.
   - Email 1 -> `Email_Subject` + `Email_Body`: max 120 words, **bold** the 3-4 strongest numbers (markdown `**`), bullets as lines starting "- ", and put `{{RESUME_LINK}}` where the CV is mentioned (build_email_html.py turns it into my RESUME_URL link).
   - Email 2 -> `Email2_Body` (max 60 words, one new proof or angle, same ask). WhatsApp 1 -> `WA_Msg` (max 60 words, one proof). WhatsApp 2 -> `WA2_Msg` (max 40). LinkedIn -> `LI_Note` (max 200 chars).
   - Founders at the same company must NOT get identical text: vary hook and proof.
5. Honesty: only resume_facts.md numbers; mark anything unverified in `Check_Before_Sending`.
6. WRITE BACK: Planned_Send (next Tue/Wed/Thu 09:30-11:00 IST, 20+ min apart; founders of the same company 1-2 days apart; max 25/day overall; unix seconds), Status = "Ready". Never set Approve (that's mine).

End with a table: founder, company, hook (1 line), anything flagged.
