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

## Step 1: Build the list of active Series A investors
Search funding-news sources (Entrackr, Inc42, VCCircle, YourStory, Crunchbase, Tracxn — free pages
only) for VCs that have actually led or co-led a Series A round in India in the last 6-7 months. Do
not use a fixed list from memory (it goes stale); build it from what you see right now. Aim for every
investor you can find evidence for, not just the famous names.
Output: a simple list, investor name + 1-2 rounds you saw them in, as your working note (not the final table).

## Step 2: Series A companies, last 6-7 months
Using that investor list plus general funding-news search ("Series A India October 2026" etc.), build
an EXHAUSTIVE table of companies that raised a Series A in the last 6-7 months (compute the exact date
window from today's date). One row per company:

| Company | Backed by | Employee size | Series A amount | Date raised | HQ | Source |
|---|---|---|---|---|---|---|

- "Backed by": the lead investor(s) you can confirm, comma separated.
- "Employee size": LinkedIn's own range (e.g. "11-50") if you can see it, else leave blank.
- "Source": the article URL you got it from.
- Skip a company if you cannot confirm BOTH the amount and the approximate date from a real source.

## Step 3: YC companies, last 6-7 months
Open ycombinator.com/companies, filter to the batches that fall in the last 6-7 months (work out which
batch codes those are from today's date — YC batches are named e.g. W26, F25, X25). Add every company
from those batches to the SAME table shape as Step 2 (Series A amount can be blank for a YC company
that didn't separately raise one; write "YC <batch>" in that column instead, e.g. "YC F25").

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

Hand the CSV to Ayush (or to Claude in his Airtable session) — it gets imported into Companies/Contacts
the same way an Apollo export always has, so it joins the one Airtable base instead of living on its own.
