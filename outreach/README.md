# Founder outreach system (Grist + Claude Code)

Zero-cost stack: Grist as the database **and** the UI, Python scripts for the deterministic parts, Claude Code for research and writing, no tracking infra.

Grist is a spreadsheet-database with its own pages, filters and charts, so there is no separate dashboard to build or host: `scripts/make_pages.py` builds the Review / Pipeline / Duplicates / Funnel / Data health pages inside the document, and you can rearrange them by clicking.

**First time on a laptop? Follow [LAPTOP_SETUP.md](LAPTOP_SETUP.md)** — every step from clone to dashboard.

## Where to run Grist

**Hosted (recommended)** — sign up at [docs.getgrist.com](https://docs.getgrist.com), create a doc called "Outreach". The link works from anywhere, your laptop can be off, and the doc can be shared. The free plan allows **5,000 rows, 10MB and 3,000 API calls a month**; this project uses about 1,000 rows and under ten calls per refresh.

**Local** — `docker compose up -d` and open http://localhost:8484. Free and private, but only up while the laptop is, and GitHub Actions cannot reach it.

Either way the scripts are the same; only `GRIST_URL` in `.env` differs.

## Setup order
1. `git init` this folder, push to your GitHub, open it with `claude`.
2. **Grist**: create an empty doc "Outreach" (hosted or local, see above).
   Profile settings > API key. `cp .env.example .env` and fill GRIST_URL, GRIST_API_KEY and GRIST_DOC_ID (the doc id is in the doc's URL).
   `python3 scripts/setup_grist.py` creates all tables and the dedupe formulas — safe to re-run, it only adds what is missing.
   `python3 scripts/make_pages.py` builds the dashboard pages.
   In Founders > Company column > Column options > SHOW COLUMN = Company (same for Founder in Outreach_Log).
3. **Gmail read access** (for "who's already emailed"): `pip install -r requirements.txt`; Google Cloud project > enable Gmail API > OAuth client (Desktop) > save as `credentials.json` in the repo root. First `gmail_sync.py` run opens a browser. Scope is read-only.
4. **Gmail drafts** via the Gmail connector in Claude Code (`/mcp`). Check the tool names with `/permissions`; if they differ from `mcp__Gmail__*` / `mcp__claude_ai_Gmail__*`, edit `.claude/settings.json` so the send/reply/forward tools stay DENIED.
5. **First task**: drop your CSV in `imports/`, run `/import-csv imports/<file>.csv`.
6. Fill the `Templates` table (email_1, email_2, wa_1, wa_2, li_note) with `{{first_name}} {{company}} {{hook}} {{proof}} {{role}}`.
7. Tracking: none for now. Reply / Sent / Bounced from Gmail are the signals.

## The dashboard pages (inside Grist)

`python3 scripts/make_pages.py` builds five pages and can be re-run at any time — it removes only the pages it made and rebuilds them, leaving your own alone.

- **Review** — one card per person waiting on you, with Approve. Filtered to Status Ready / Contact-ready and not a duplicate.
- **Pipeline** — counts per status, then the full list.
- **Duplicates** — whatever the `Is_Dup` formula caught.
- **Funnel** — bar charts: by stage, by status, by designation, companies by funding stage and by source.
- **Data health** — what the last import found wrong, and the Refresh_Runs log (including the API calls each run spent).

## Importing the outreach tracker workbook

`python3 scripts/import_tracker.py --file imports/<file>.xlsx [--dry-run]`

The workbook needs real repair before it can be read, and `scripts/tracker_clean.py` does it: Master's header is one tab-joined cell; Master row 72 holds 17 lines of leads crushed into a single cell; and the Followups sheet is three pasted blocks at different column offsets, not one table. Some lead rows were also pasted in a different column order — those are re-read by layout rather than dropped.

Three of the workbook's tables describe the same people, so they are merged on the email address **before** anything is written: one person becomes one Founders row, and `Is_Dup` stays at zero. Companies named anywhere but missing from the Companies sheet are created as bare rows. Re-running changes nothing, and your own edits to Approve / Status / Notes are never overwritten once set.

Funding stage arrives written 18 different ways ("Seed / Bootstrapped", "Series A / YC W21"); it is tidied to one value for charting, with the original kept in `Stage_Raw`.

## Keeping it current

`python3 scripts/refresh.py --apply` picks up anything new in `imports/` (by file hash, so unchanged files cost nothing) and then asks Gmail what happened to the mail. It writes a row to `Refresh_Runs` each time and exits non-zero if it could not do its job.

On a schedule, `.github/workflows/outreach-refresh.yml` runs it three times a day (09:07, 14:07, 20:07 IST). Repository secrets: `GRIST_URL`, `GRIST_API_KEY`, `GRIST_DOC_ID`, and for the Gmail step `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`. Without the Google ones it still imports files and skips Gmail.

### Getting a Gmail refresh token (once, on your laptop)

GitHub Actions has no browser, so the one-time sign-in happens locally and the result is stored as a secret.

1. Google Cloud Console > enable the Gmail API > OAuth client ID > **Desktop app** > download as `credentials.json` into this folder.
2. `pip install -r requirements.txt`, then `python3 scripts/gmail_sync.py --dry-run` and sign in. This writes `token.json`.
3. Read the three values out of those files — client id and secret from `credentials.json`, `refresh_token` from `token.json` — and put them in the repository secrets above.

`credentials.json` and `token.json` are gitignored. The scope is read-only; nothing in this repo can send mail.

## Control panel (optional, local only)
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
