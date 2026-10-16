# Founder's Office outbound

Spec: `REQUIREMENTS.md` (current). One stdlib-only file, `outbound.py`; state in `queue.csv` (gitignored).
Nothing is sent without `approved=y`. Plain text, no tracking, resume link only in the last note.

## Setup
1. Gmail: enable IMAP, create an app password (needs 2-step verification).
2. `cp .env.example .env`, fill in `GMAIL_USER` and `GMAIL_APP_PASSWORD` (`.env` is gitignored; on Mac/Linux also `chmod 600 .env`). `outbound.py` and `ui.py` read it automatically. Then `python outbound.py check` tests the login without sending anything.
3. Apollo: export People CSV (seed / pre-seed, 2 to 50 employees, Founder / Co-Founder / CEO).
4. `python outbound.py draft apollo.csv`, then `python outbound.py cycle --dry` to read what would happen.

## UI (easiest way)
```
export GMAIL_USER=you@gmail.com GMAIL_APP_PASSWORD=xxxx
python ui.py     # open http://127.0.0.1:8765
```
Upload the Apollo CSV, relevant rows are drafted automatically, edit the opening line, tick and approve, then
"Send approved + follow-ups". Local only (127.0.0.1), the upload is deleted after reading, nothing sends without
approval. Do not run the UI and the cron `cycle` at the same moment (both write queue.csv).

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
