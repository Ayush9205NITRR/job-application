# Founder outreach system (Grist + Claude Code)

Zero-cost stack: Grist (local Docker) as the database/UI, Python scripts for the deterministic parts, Claude Code for research and writing, no tracking infra.

## Setup order
1. `git init` this folder, push to your GitHub, open it with `claude`.
2. **Grist**: put your email in `docker-compose.yml`, `docker compose up -d`, open http://localhost:8484, create an empty doc "Outreach".
   Profile settings > API key. `cp .env.example .env` and fill GRIST_API_KEY and GRIST_DOC_ID (from the doc URL).
   `python3 scripts/setup_grist.py` creates all tables and the dedupe formulas.
   In Founders > Company column > Column options > SHOW COLUMN = Company (same for Founder in Outreach_Log).
3. **Gmail read access** (for "who's already emailed"): `pip install -r requirements.txt`; Google Cloud project > enable Gmail API > OAuth client (Desktop) > save as `credentials.json` in the repo root. First `gmail_sync.py` run opens a browser. Scope is read-only.
4. **Gmail drafts** via the Gmail connector in Claude Code (`/mcp`). Check the tool names with `/permissions`; if they differ from `mcp__Gmail__*` / `mcp__claude_ai_Gmail__*`, edit `.claude/settings.json` so the send/reply/forward tools stay DENIED.
5. **First task**: drop your CSV in `imports/`, run `/import-csv imports/<file>.csv`.
6. Fill the `Templates` table (email_1, email_2, wa_1, wa_2, li_note) with `{{first_name}} {{company}} {{hook}} {{proof}} {{role}}`.
7. Tracking: none for now. Reply / Sent / Bounced from Gmail are the signals.

## Control panel (the easy way to see and do everything)
`python3 ui/server.py` then open http://localhost:8585
- **Dashboard**: funnel (people -> contactable -> approved -> sent -> delivered -> viewed -> replied), due follow-ups, by vertical/stage.
- **Upload**: pick your CSV, check the column mapping (dropdowns), Import. Optional views CSV.
- **Contacts**: search/filter, tick Approve (single or bulk), click a person to edit all messages, open WhatsApp with text filled, copy text.
- **Follow-ups**: what is due today (Email 2, WhatsApp 1, WhatsApp 2) with one-click buttons.
- **Sync**: Gmail check -> writes Sent / Delivered / Viewed / Replied into Grist.
It never sends email. Drafts come from `/run-c` in Claude Code; you send in Gmail.

## Sent -> Delivered -> Viewed -> Replied (`scripts/reverse_map.py`)
Sent = mail in your Sent folder. Bounced = mailer-daemon reply in the thread. Replied = someone else wrote in the thread (out-of-office ignored).
Delivered = INFERRED (Gmail has no delivery receipt): sent, no bounce after 1 hour.
Viewed = only if you give it a views CSV (any tracker export with an email column) in `imports/views*.csv` or via the Upload tab. Without it, Viewed stays empty; a reply implies it was read.

## Follow-up cadence (business days; stops on reply / bounce / skip)
Day 0 Email 1 -> Day 1+ LinkedIn note (manual) -> Day +3 WhatsApp 1 -> Day +5 Email 2 (same thread) -> +3 after Email 2: WhatsApp 2 -> end.
Due dates are Grist formula columns: Sent2_Due, WA1_Due, WA2_Due.

## Daily loop
`/run-a` (jobs + founders) -> Apollo export into `imports/` (manual) -> `/run-b` (personalise) -> you review in Grist and tick Approve -> `/run-c` (sync + drafts) -> you send/schedule in Gmail -> next `/run-c` (or the Sync tab) maps Sent / Delivered / Viewed / Replied / Bounced.

## Grist pages to make (no code)
- **Review**: Founders card list, filter Status = Ready and Is_Dup = false; show Company_Name, Name, Research_Hook, Email_Subject, Email_Body, WA_Link, LI_Note, Check_Before_Sending, Approve. Click WA_Link = WhatsApp opens with text prefilled.
- **Pipeline**: Founders grid grouped by Status. **Duplicates**: filter Is_Dup = true. **Funnel**: Founders summary by Company.Vertical / Company.Stage / Status.

## Dedupe (formula columns on Founders, automatic)
Email_norm (lowercase, drop +tag, gmail dots), WA_norm (digits, 91 default, 11-15 digits), LI_norm (no protocol/query/slash). `Is_Dup` = true on the LATER row of any pair sharing any of the three. Gmail-side gate (`gmail_sync.py --gate`) runs before every draft.

## Tracking (none on purpose)
No pixels, no redirect links. `gmail_sync.py` gives Mails_Sent, Sent-1/Sent-2, Replied, Bounced. If you later want resume views, the open-source options are Papermark (hosted free tier or self-host; per-link view analytics for a PDF) or Shlink (needs a public server); both are optional and not wired in.

## Tests
`python3 tests/test_all.py` and `python3 tests/test_ui.py` (formulas, dedupe, CSV mapping, HTML builder, UI server, reverse-map logic, follow-up dates).

## Not automated on purpose
WhatsApp sending, LinkedIn actions, and any email send.
