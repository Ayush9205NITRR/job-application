# Founder's Office outbound system

Scrape Founder's Office jobs, score companies against an ICP, enrich decision makers, draft personalised Gmail
drafts, and track everything in Airtable. **Nothing is sent or submitted by code.** Gmail output is drafts only
(`fo/gmail.py` never calls send); Ayush reviews and sends by hand.

## Setup
1. `pip install -r requirements.txt`, `cp .env.example .env`, fill in keys. Use **personal** Apollo and Airtable
   accounts, not Enout's.
2. Gmail: Google Cloud project, enable Gmail API, create an OAuth *Desktop* client, save it as `credentials.json`.
   First run opens a browser to authorise (`gmail.compose` + `gmail.readonly`).
3. Airtable base with the six tables from the spec. Primary field of **Companies must be `Company LinkedIn URL`**.
   Two small additions to the spec schema: Companies gets `About` (long text), Contacts gets optional `First line`
   (long text). Link fields: Jobs.`Company LinkedIn URL` -> Companies, Contacts.`Company` -> Companies,
   Touches.`Contact` and Replies.`Contact` -> Contacts. Date fields (`Drafted at`, `Sent at`, `Received at`,
   `Posted`) should be date with time. Single-select options are created automatically.
4. Copy `profile.example.json` to `profile.json` (gitignored). No health information.

## Run order
| Script | When | What |
|---|---|---|
| `python pipeline.py` | weekly | scrape, dedupe, hard excludes, Gemini score, upsert (>=60 = Shortlisted) |
| `python enrich.py` | after pipeline | Apollo on Shortlisted only, verified emails only, + job poster |
| apply by hand | | open job link, autofill, submit, tick **Applied** on the Job |
| `python draft.py` | daily | Gmail drafts for new contacts (only where a job is ticked Applied) |
| send by hand | 9 to 11 AM IST | review drafts, send |
| `python followup.py` | daily | marks sent touches, drafts day 5 / day 12 in-thread, stops on reply |
| `python replies.py` | daily | logs replies, flags bounces, classifies, drafts a response |

All scripts take `--dry-run`. Try `python draft.py --dry-run` first to read the rendered emails.

## First run checklist
- `python pipeline.py --dump data/raw.json --dry-run`, then inspect the keys. `normalise()` in `pipeline.py`
  tries several likely field names but I could not see the actor's real output; adjust there. Also check
  `apify_input.json` (optional, overrides `DEFAULT_INPUT`) against the actor's input schema.
- Apollo endpoints follow the current docs (`mixed_people/api_search`, `people/match`). Revealing an email
  spends credits, so `enrich.py` defaults to 15 companies per run.

## Guardrails in code
Daily cap 5/10/15/20 by week since first send, halt on >3% bounces (min sample 10, or 2+ bounces), verified
emails only, never Do not contact, first line rejected if flattery, dashes, 20+ words or health terms, missing
template variables abort the draft. Tests: `pytest`.

## Not built yet
Chrome autofill extension (spec step 6). Use Simplify with `profile.json` as the reference until then.
