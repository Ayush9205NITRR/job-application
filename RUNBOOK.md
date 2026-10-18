# Runbook: the same every day

Times are IST. GitHub timers need this repo's default branch (merge once); until then press the buttons.

| Time | What | Who |
|---|---|---|
| every 3rd day 08:00 | Fetch new companies (Apify, about 0.50 USD) | GitHub timer (`schedule.yml`) |
| hourly 08:30 to 13:30 | Make tasks, apply answers, Emails rows, follow-ups | GitHub timer |
| 09:00 daily | Start the agent with `AGENT.md` (runs up to 3 hours, stops after 15 quiet minutes) | You |
| 13:00 | In Airtable Contacts, add Email + Email status = verified for new LinkedIn people | You |
| 14:00 daily | Start the agent again: makes the Gmail drafts for the emails you added | You |
| evening | Gmail Drafts: read, press Send | You |

Why the agent runs every day even though Apify runs every 3rd day: the agent also checks what was sent and replied, and
makes follow-up drafts. On days with no new work it finishes in 15 minutes.

## Before anything: look at `Your tasks`
Airtable `Your tasks`, or the failed-run email from GitHub. Each row says what to do. Set it Done when finished.

## First time only
1. Secrets: `AIRTABLE_TOKEN`, `APIFY_TOKEN` in GitHub Actions (and Codespaces if used).
2. Run workflow `Daily schedule` with job = round. It creates tables and columns and runs `preflight.py`.
3. Start the agent.

## If something looks wrong
`python preflight.py` names the problem. Past mistakes and the check that now catches each are in `MISTAKES.md`.
Rule: nothing new gets built while an agent run is live.
