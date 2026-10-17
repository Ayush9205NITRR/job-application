# Founder's Office outbound

Spec: `REQUIREMENTS.md` (current). One stdlib-only file, `outbound.py`; state in `queue.csv` (gitignored).
Nothing is sent without `approved=y`. Plain text, no tracking, resume link only in the last note.

## Setup
1. Gmail: enable IMAP, create an app password (needs 2-step verification).
2. `cp .env.example .env`, fill in `GMAIL_USER` and `GMAIL_APP_PASSWORD` (`.env` is gitignored; on Mac/Linux also `chmod 600 .env`). `outbound.py` and `ui.py` read it automatically. Then `python outbound.py check` tests the login without sending anything.
3. Apollo: export People CSV (seed / pre-seed, 2 to 50 employees, Founder / Co-Founder / CEO).
4. `python outbound.py draft apollo.csv`, then `python outbound.py cycle --dry` to read what would happen.

## The flow (one page, `python ui.py`)
1. **Scrape jobs**: Apify finds Founder's Office jobs (8 keywords by default, up to 300 jobs each, past month, 3 keywords at a time), companies + jobs go to Airtable (needs `APIFY_TOKEN`). LinkedIn *posts* need a second Apify actor, not connected yet.
2. **Apollo CSV**: drop the account export (Company Linkedin Url or Website, Funding, Funding Stage, Revenue, Employees). It is matched onto the scraped companies.
3. **ICP and overlap**: live filter on stage, employees, revenue, funding (default seed / pre-seed, 2 to 50 employees; optional toggle to include companies with no funding data, only if their headcount is known and in range). Matches are ticked and highlighted. "Check overlap" reads each company's public site (home, customers, case studies, about; robots.txt respected, 8 in parallel) for **your clients** (editable list, `Name | alias`, `#` to leave one out). Matches become the pitch, click a name to leave it out. Also: calendar blocks (.ics) for applying, mark applied.
4. **Contacts**: drop `Company Linkedin, POC First Name, POC Email, POC Contact Detail`. Only selected companies are added, as drafts. Companies with overlap get a pitch ("I saw X works with A and B. I have worked with them too."), the rest the generic email.
5. **Review and send**: edit the opening line, approve (keys j k a s e x), send. Follow-ups go out on day 5 and day 12. Nothing is sent without approval and a Send click.

Needs `AIRTABLE_TOKEN` and `AIRTABLE_BASE_ID` for steps 1 to 4, `GMAIL_USER` / `GMAIL_APP_PASSWORD` for sending, `pip install requests`.
It is fast because the page never reloads, Airtable is read once and cached, and edits are applied in the browser first and saved in the background.

## UI (easiest way)
```
export GMAIL_USER=you@gmail.com GMAIL_APP_PASSWORD=xxxx
python ui.py     # open http://127.0.0.1:8765
```
Upload the Apollo CSV, relevant rows are drafted automatically, edit the opening line, tick and approve, then
"Send approved + follow-ups". Local only (127.0.0.1), the upload is deleted after reading, nothing sends without
approval. Do not run the UI and the cron `cycle` at the same moment (both write queue.csv).

### Secrets not found in the Codespace?
Run `python outbound.py env`. It lists which secrets this terminal sees (names and lengths, never values) and explains the fix.
Codespaces read secrets only when the codespace starts: add or change them, **stop** the codespace, reopen it, use a **new** terminal.
Each secret's "Repository access" must include this repo. Quick workaround: `cp .env.example .env` and fill it in (gitignored).

### UI in GitHub Codespaces (no install on your laptop)
1. github.com/settings/codespaces -> New secret: `GMAIL_USER` and `GMAIL_APP_PASSWORD`, repository access: this repo.
2. Repo page -> Code -> Codespaces -> Create codespace on branch `claude/confident-knuth-cdvylw`.
3. The UI starts by itself and opens in a browser tab (private port, only you can open it). If not: Ports tab -> 8765.
`queue.csv` lives inside the codespace (gitignored). The codespace sleeps after idle, files stay until you delete it.

## Daily loop
`python outbound.py cycle` = inbox + send + followup + notify. Run it at 9 AM and 5 PM IST on any always-on machine:
```
30 3,11 * * *  cd /path/to/job-application && python3 outbound.py cycle >> outbound.log 2>&1
```
Reply to the `[Outbound] Action needed` digest with: `approve 1 3 5` | `approve all` | `line 2: your opening line` |
`skip 4` | `replied 6`. Only replies from your own address that pass SPF/DKIM are obeyed, each once, and
`approve all` covers only the rows listed in that digest.

## Rules enforced in code
Cap 5/day for the first 3 days after the first send, then 20. Random 2 to 5 minute gap. Verified emails only.
Hard excludes (MICE, events, staffing, manpower) on company name and industry. Sending halts on 2+ bounces,
or above 3% once 10 are sent. Replies and bounces are detected over IMAP and stop that row.
Follow-ups (day 5, day 12) thread to the first message. WhatsApp links appear in the digest on day 3.

## Tests
`python -m pytest` (smtplib and imaplib are faked; nothing touches the network).

## Not built yet (REQUIREMENTS section 10)
Step 7 personal line via Gemini (`line` defaults to a generic sentence; override per row with `line N: ...`).
Steps 3 to 4 are live checks that need your Gmail: one email to a second address, a shortened `FU1_DAYS`
to confirm threading, then each reply command.

## Legacy
The earlier Airtable / Apify / Apollo-API / Gmail-draft design (`pipeline.py`, `enrich.py`, `draft.py`, `followup.py`,
`replies.py`, `linkedin.py`, `setup_airtable.py`, `fo/`, `.github/workflows/`) is superseded by the doc above.
It is untouched but unused; say the word and it gets deleted.
