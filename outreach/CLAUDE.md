# Founder outreach system (Ayush) — project rules

I'm Ayush, job hunting. This repo runs a cold-outreach system aimed at founders of funded startups (Travel, Edtech; Seed / Series A / Series B; raised in the last 24 months). Goal of every message: get a REPLY from the founder that moves me toward being hired onto their team.

## Architecture
- **Grist** = the database AND the UI (hosted at docs.getgrist.com, or local Docker on :8484 — only `GRIST_URL` differs). Tables: `Companies`, `Founders`, `Templates`, `Outreach_Log`, `Data_Issues`, `Refresh_Runs`. Schema: `scripts/setup_grist.py`. Dashboard pages (Review / Pipeline / Duplicates / Funnel / Data health): `scripts/make_pages.py`. Both are safe to re-run.
- **Scripts** (`scripts/`) do everything deterministic: `import_csv.py` (Apollo CSVs), `tracker_clean.py` + `import_tracker.py` (the outreach tracker workbook), `reverse_map.py` + `gmail_sync.py` (read-only Gmail API), `refresh.py` (scheduled), `build_email_html.py`, `grist_client.py`.
- **Control panel** (`ui/server.py`, http://localhost:8585): optional, local only. Grist's own pages are the dashboard.
- **Claude** (you) does the judgement work: research, intersection, writing, reviewing, summarising. Slash commands: `/import-csv`, `/run-a`, `/run-b`, `/run-c`.
- Gmail drafting goes through the Gmail connector (draft only). WhatsApp and LinkedIn are NEVER automated.

## Hard rules (never break)
1. NEVER send an email. Draft only. The Gmail send/reply/forward tools are denied in `.claude/settings.json`; do not look for workarounds.
2. NEVER write to formula columns: Email_norm, WA_norm, LI_norm, Is_Dup, WA_Link, WA2_Link, Sent2_Due, Funded_24mo, Founders_Count, Company_Name. `grist_client.py` refuses.
3. NEVER email/message a row with Is_Dup = true, Status in (Skip, Replied, Bounced), or Reply = Unsubscribed.
4. NEVER guess or pattern-generate an email address or phone number. Contact data comes only from my CSV / Apollo exports in `imports/`.
5. NEVER scrape LinkedIn through a logged-in browser (account risk). LinkedIn URLs come from web search, company pages, or my exports. Connection notes are text I paste myself.
6. NEVER delete rows, drafts, or mail. NEVER touch rows outside the Status filter of the command you're running.
7. Work in batches (BATCH_SIZE default 10; 25 drafts/day max). On a row error: write the reason to Notes, continue.
8. Dates/DateTimes over the Grist API are unix seconds (`ts_date`, `ts_now` in grist_client.py).
9. Grist's free plan allows 3000 API calls a month for the WHOLE site. Batch reads and writes; `grist_client.calls()` reports the count and `refresh.py` records it in `Refresh_Runs.Api_Calls`. A scheduled refresh runs 3x/day and costs under ten calls.
10. An import must never clobber my own edits: `Approve`, `Status`, `Notes`, `Check_Before_Sending` and the Sent flags are left alone once they hold a value (`USER_OWNED` in import_tracker.py).

## Voice
Plain, humble, crisp, slightly witty. Never cocky or salesy, never overclaim ("completely off your plate" was rejected). One easy ask: "Could we have a quick chat?".

## Honesty rules (resume)
- Source of truth: `resume_facts.md`. Use ONLY numbers in it. Don't round, invent, or add "+" that isn't there.
- Enout is always "a MICE company and a Stanford Seed company", and I'm "currently" there (last day 1 Jan 2027, but the copy says "currently"). Never "ex-Enout".
- The BD console: say "AI-assisted" or "built with Claude Code", and that I owned the spec, testing and rollout.
- Sector moves (MICE -> travel/edtech, B2B -> B2C): don't hide it; frame the transferable part.
- Never state an unverified fact. If a fact is only from a press release or contributor piece, say so. Anything unverified goes into `Founders.Check_Before_Sending`.
- Do not mention an AlgoUniversity employee number while resume_facts.md has the NEEDS CONFIRMATION line.
- Email: max 120 words, bold the 3-4 strongest numbers, mention CV attached (or the RESUME_URL link). WhatsApp: max 60 words. LinkedIn note: max 200 chars.

## Follow-up cadence (business days)
Day 0 Email 1 -> Day 1+ LinkedIn note -> +3 WhatsApp 1 -> +5 Email 2 (same thread) -> +3 after Email 2 WhatsApp 2 -> stop. Stops on Reply / Bounced / Unsubscribed / Skip. Due dates: Sent2_Due, WA1_Due, WA2_Due.

## Funnel / status flow (Founders.Status)
New -> Contact-ready -> Ready -> (I tick Approve) -> Drafted-1 -> Sent-1 -> Drafted-2 -> Sent-2 -> Replied | Bounced | Skip

## Tracking
No open pixels, no redirect links, no tracking infrastructure (my decision). Signals are Gmail-derived: Stage (Sent / Delivered(inferred) / Replied / Bounced), Mails_Sent, Sent-1/Sent-2 via reverse_map.py. "Viewed" only from a views CSV I provide. Never add tracking pixels or wrapped links to drafts.

## Config
Secrets/URLs live in `.env` (see `.env.example`). Never commit `.env`, `credentials.json`, `token.json`, or CSVs in `imports/`.
