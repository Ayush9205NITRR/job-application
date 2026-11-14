---
description: Sourcing run — companies -> jobs -> founders (names, designations, LinkedIn URLs)
argument-hint: [DISCOVER vertical=Travel|Edtech stage=Seed|"Series A"|"Series B" count=N]  (empty = process Companies with Status New)
---

RUN A: SOURCING. Arguments: $ARGUMENTS

Mode 1 (default): process `Companies` rows with Status = "New" (fetch with `python3 scripts/grist_client.py fetch Companies '{"Status":["New"]}'`). Max BATCH_SIZE (10).

Mode 2 (only if the arguments start with DISCOVER): find Indian startups in that vertical that raised that stage in the last 24 months. Use WebSearch/WebFetch (Entrackr, Inc42, Tracxn, company blogs, press). For each: verify the round date and keep the source URL; if you can't verify the date, do not add it. Before adding, check Companies for the same name or website domain (case-insensitive) and skip if present. Add rows with Status = "New", Funding_Source, Last_Round_Date (unix seconds), Round_Amount, Vertical, Stage.

For each Company with Status = "New":
1. JOB: find the open role that fits Founder's Office / Strategy & Ops / Growth / BD (careers page, job boards via search). Put the link in Job_Link and the title in Role_Target. If none open, leave blank (outreach then asks if a role is open).
2. PEOPLE: find up to 3 people titled Founder, Co-Founder, CEO or COO. Sources: company site, news, web search. Record Name, LinkedIn URL (public profile URL found via search / page links; do NOT use a logged-in LinkedIn session), Designation (exactly one of Founder / Co-Founder / CEO / COO), Company = the company row id.
3. CONTACT: never guess email or phone. Only fill Email / WhatsApp from `./imports/*.csv` (Apollo exports etc.), matching on normalised LinkedIn URL first, then exact name + company. No match -> leave blank, Notes = "needs Apollo export".
4. Insert via `grist_client.py add Founders ...`, re-fetch those rows: if Is_Dup is true -> Status = "Skip", Notes = "duplicate of an earlier row". Rows with Email_norm or WA_norm -> Status "Contact-ready"; others "New".
5. Company Status -> "Founders found" (or "Skip" + Notes if no founders identified).

End with: companies done, founders added, founders still missing contact details (so I can export from Apollo), and any facts you couldn't verify.
