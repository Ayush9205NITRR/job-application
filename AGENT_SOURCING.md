# Agent: Series A + YC sourcing sweep (one-off, not the daily loop)

This is a SEPARATE job from `AGENT.md`. Give the agent ONLY this file for this run. Do not run this
at the same time as the daily `AGENT.md` loop (both touch Airtable; keep them to separate sessions).

Why separate: `AGENT.md` is the recurring loop (LinkedIn jobs/posts, never collects emails). This is a
one-off exhaustive sourcing sweep (Series A + YC, in the last 6-7 months), and it DOES use Apollo to
pull emails — Ayush has approved that spend in writing, for this module only, email only (see Step 5).

## What you are building
One exhaustive list of companies that are either:
(a) Series A funded in the last 6-7 months, or
(b) in a Y Combinator batch from the last 6-7 months,
then the founders of each, then a personalised email per founder. Target: Ayush is looking for a B2B
sales / Founder's Office / Chief of Staff role, so these are target employers, not customers.

## Hard rules (same spirit as AGENT.md)
1. NEVER send an email. Only create Gmail DRAFTS, never press Send.
2. Apollo: ONLY use it to find a decision maker's EMAIL ADDRESS. Do NOT extract, click to reveal, or
   write down any PHONE NUMBER. Do not click "Get phone", "Call", "Mobile number" or similar. If a
   contact's email needs its own separate credit from its phone, take the email only.
3. No other credit spend anywhere (Crunchbase, Tracxn, LinkedIn Premium, paid exports) without a new
   written approval from Ayush. Free-tier browsing and reading is fine everywhere.
4. Everything you find is a claim to verify, not an assumption. If you cannot confirm a figure, leave
   the cell blank rather than guess.

## Why this must be exhaustive, not a sample
A single search ("Series A India funding") gives you one page of results and you will stop there —
that is NOT exhaustive, it's whatever the search engine ranked first. "Exhaustive" means: every month
in the window, swept on more than one source, with pagination pushed to its end, not just the first
page. Budget real effort here: this step alone should take you many searches, not three or four.

## Step 0: Set the exact window
Compute today's date minus 6 and minus 7 months as two calendar dates, e.g. "between 2026-03-20 and
2026-10-20". Write this down and use it literally in every search below ("Series A India March 2026",
"...April 2026", … one search per month, not one search for the whole range). A single combined search
undercounts; a monthly sweep does not.

## Step 1: Sweep funding news month by month
For EACH month in the window, on EACH of these sources, search and go through every page of results
(not just the first page — click "next" / "load more" until it stops giving new rows):
- Entrackr funding tracker / tag pages
- Inc42 funding news archive (filter by month if the site allows it)
- VCCircle funding news
- YourStory funding news
- Crunchbase "India Series A" filtered funding round list (free view)
- Tracxn's public funding news page
That is 6 sources × 6-7 months = 36-42 sweeps minimum. Do not skip a month because an earlier month
was slow — rounds are not evenly spread, and a skipped month is a real gap, not noise.
As you go, build the investor list as a side effect (every lead investor you see, India, Series A,
in-window) rather than as a separate upfront step — you cannot know who is "active" without doing this
sweep anyway, so do not waste a pass guessing a VC list first.

## Step 2: Series A companies, last 6-7 months
From everything found in Step 1, build ONE table, one row per company (merge duplicates you saw on
two sources into one row, keep both sources):

| Company | Backed by | Employee size | Series A amount | Date raised | HQ | Source |
|---|---|---|---|---|---|---|

- "Backed by": the lead investor(s) you can confirm, comma separated.
- "Employee size": LinkedIn's own range (e.g. "11-50") if you can see it, else leave blank.
- "Source": every article URL you got it from (can be more than one).
- Skip a company if you cannot confirm BOTH the amount and the approximate date from a real source.
- Before you call this table done: pick 2 of the months and re-run just the Entrackr and Inc42 search for
  that month a second time. If you find even one company you missed, you stopped too early — go back
  and finish the sweep for the other months too before moving on.

## Step 3: YC companies, last 6-7 months
Open ycombinator.com/companies (not a search engine search for it — the real directory page). Work out
which batch codes fall in the last 6-7 months from today's date (YC batches: W = winter, S or X =
summer, F = fall, e.g. F25, X25, W26). Filter the directory to each of those batches ONE AT A TIME and
scroll/paginate to the end of that batch's full list — a YC batch can be 200+ companies, so the first
screen is a small fraction. Do this for every batch in the window, not just the latest one. Add every
company from those batches to the SAME table shape as Step 2 (Series A amount can be blank for a YC
company that didn't separately raise one; write "YC <batch>" in that column instead, e.g. "YC F25").
Record, per batch, the TOTAL company count the directory shows for that batch, and how many rows you
actually captured — if those two numbers don't match, you are not done with that batch.

## Step 4: Dedupe against what Ayush already has
Before adding a company, check it isn't already in his Airtable Companies table (same rule as
AGENT.md): normalise the company's LinkedIn URL and skip it if that URL is already listed. Note the
skip in your own working list, don't put it in the final table twice.

## Step 5: Founders, then their email via Apollo
For every company in the final table, find the founder(s) and co-founder(s) from the company's LinkedIn
page or website (same as `pick_decision_makers` in AGENT.md — up to 3 people, founders first, then HR/TA).
THEN, for each person, open Apollo (Ayush will give you the Enout Apollo login) and find their email
only — never a phone number (rule 2 above). If Apollo doesn't have them or you're not signed in, leave
the email blank and note "no Apollo match" rather than guessing an address.

Add two columns to each person: `LinkedIn URL`, `Email` (blank if not found).

## Step 6: Personalised email per founder
For each founder with an email, draft ONE email using Ayush's resume/CV (ask Ayush for the file or link
if you don't have it; do not use a generic line). The email should:
- Open with one specific, true thing about THIS company (what they do, the funding news itself, a
  product or hire you saw) — not a generic compliment.
- Say in 1-2 lines what Ayush does (B2B sales / revenue systems / Founder's Office work) and why that's
  useful for an early-stage team right after a raise.
- End with a soft ask for a 15-minute call, and a link to his resume.
- Keep it under 120 words. No em dashes.
Create it as a Gmail DRAFT only (never send). Subject: personal, not generic ("Reaching out after your
Series A" style, not "Opportunity").

## Deliverable
1. The final table from Steps 2-3 (with Step 5's LinkedIn/Email columns added) as one CSV, columns in
   this exact order: Company, Backed by, Employee size, Series A amount, Date raised, HQ, Source,
   Founder name, Founder LinkedIn, Founder email.
2. The Gmail drafts from Step 6 (left in Drafts, never sent).
3. A short note of anything you skipped and why (no Apollo match, couldn't confirm the round, etc.).
4. A coverage report: how many rows came from each month and each source (Step 1), and for YC, the
   directory's own count vs your captured count per batch (Step 3). If any month shows 0 rows from a
   source that should have some, say so — that is a real gap to go back and check, not a clean result.

Hand the CSV to Ayush (or to Claude in his Airtable session) — it gets imported into Companies/Contacts
the same way an Apollo export always has, so it joins the one Airtable base instead of living on its own.
