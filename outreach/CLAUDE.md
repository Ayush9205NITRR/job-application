# Founder outreach system (Ayush) — project rules

I'm Ayush, job hunting. This repo runs a cold-outreach system aimed at founders of funded startups (Travel, Edtech; Seed / Series A / Series B; raised in the last 24 months). Goal of every message: get a REPLY from the founder that moves me toward being hired onto their team.

## Architecture
- **Grist** (local Docker, http://localhost:8484) = the database + UI. Tables: `Companies`, `Founders`, `Templates`, `Outreach_Log`. Schema is created by `scripts/setup_grist.py`.
- **Scripts** (`scripts/`) do everything deterministic: `import_csv.py`, `gmail_sync.py` (read-only Gmail API), `build_email_html.py`, `grist_client.py`.
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

## Funnel / status flow (Founders.Status)
New -> Contact-ready -> Ready -> (I tick Approve) -> Drafted-1 -> Sent-1 -> Drafted-2 -> Sent-2 -> Replied | Bounced | Skip

## Tracking
No open pixels, no redirect links, no tracking infrastructure (my decision). Signals are Gmail-derived only: Mails_Sent, Sent-1/Sent-2, Reply, Bounced (via gmail_sync.py). Never add tracking pixels or wrapped links to drafts.

## Config
Secrets/URLs live in `.env` (see `.env.example`). Never commit `.env`, `credentials.json`, `token.json`, or CSVs in `imports/`.
