# REQUIREMENTS: Founder's Office outbound (job-outreach)

Owner: Ayush Tiwari. Goal: land a B2B sales / BD / Founder's Office / Chief of Staff role at a seed or pre-seed startup. Notice: LWD 1 Jan 2027.
Differentiator: revenue systems. Proof points: BD desk 0 to Rs 40 Cr GMV quarter and conversion 7% to 20% (Enout); hired 7 in 30 days (Enout); 12th employee at AlgoUniversity (YC S21), vertical to Rs 9 Cr, Rs 2.7 Cr closed personally at 40% conversion.

## 1. Principles
- One lean Python file (`outbound.py`), stdlib only. No Airtable, no n8n, no paid tools. State lives in `queue.csv`.
- Only one manual data input: an Apollo People CSV export. Every other human step arrives as an email to Ayush, and he answers by replying to it.
- Nothing is sent without `approved=y`. Plain text, no tracking pixels, no links except the resume in the last note.
- WhatsApp is never auto-sent. The digest carries a `wa.me` link with the text filled; Ayush taps send.
- Sending from primary Gmail via SMTP app password. Protect its reputation: cap 5/day first 3 days, then raise to max 20; random 2 to 5 min gap; verified emails only; stop if bounces pass 3%.
- Never name a client unless the Proof list says the name is usable. No invented clients or numbers. No em dashes in copy.
- Never commit `queue.csv`, any CSV, or credentials. Repo is private.
- Ayush keeps this on his own accounts, separate from Enout's tools, until 1 Jan.

## 2. Flow
1. **Apollo export (manual).** Saved search: seed / pre-seed, 2 to 50 employees, titles Founder, Co-Founder, CEO. Export People CSV with email, phone, company LinkedIn URL, about.
2. `python outbound.py draft apollo.csv` builds `queue.csv`.
3. `python outbound.py notify` emails Ayush a digest.
4. Ayush replies to the digest. `python outbound.py inbox` applies his reply to `queue.csv`.
5. `python outbound.py send` sends approved rows.
6. `python outbound.py followup` sends day 5 and day 12 in the same thread (approved rows, no reply).
7. Day 3 after first email, the digest includes the WhatsApp link.
8. A prospect reply is auto-detected through IMAP: follow-ups and WhatsApp stop, Ayush gets "[Outbound] REPLY from X".
9. `python outbound.py cycle` = inbox + send + followup + notify. Scheduled twice a day (9 AM, 5 PM IST).
10. Refill nudge: fewer than 10 unsent rows left, the digest says "export next Apollo batch".

## 3. Commands
| Command | What it does |
|---|---|
| `draft <csv>` | Map Apollo CSV into queue.csv. Dedupe on email. Skip unverified email and MICE / events / staffing / manpower. Build subject, body, fu1, fu2, wa_link |
| `send [--dry]` | Send rows with `approved=y` and no `sent_at`. Respect DAILY_CAP and the random gap. Save `message_id` |
| `followup [--dry]` | fu1 at day 5, fu2 at day 12, `In-Reply-To` and `References` set to the first message_id. Skip replied/skipped rows |
| `wa` | Print WhatsApp links due (day 3) and mark `wa_shown` |
| `notify [--dry]` | Email Ayush the digest (drafts to approve, follow-ups due, WhatsApp links) |
| `inbox` | Read Ayush's replies to the digest, apply commands; detect prospect replies by sender |
| `cycle [--dry]` | inbox, send, followup, notify |

## 4. queue.csv columns
approved, email, first, company, title, phone, about, line, subject, body, fu1, fu2, wa_link, sent_at, fu1_at, fu2_at, wa_shown, message_id, replied
`replied` values: blank, `y` (they replied), `skip` (Ayush dropped it), `bounced`.

## 5. Apollo CSV input
Headers are matched loosely (lowercase, symbols stripped). Needed: Company Name, Company Linkedin Url, Website, Short Description (or SEO Description, Keywords), # Employees, Industry, Latest Funding, First Name, Last Name, Title, Email, Email Status, Mobile Phone or Work Direct Phone, Person Linkedin Url. Phones become 91XXXXXXXXXX. Only `Email Status = Verified`.

## 6. Digest reply grammar
One command per line, case-insensitive; quoted lines starting with `>` are ignored.
```
approve 1 3 5        | approve all
line 2: custom opening line      (replaces the line in row 2's body)
skip 4
replied 6
```
Numbers are row numbers in queue.csv (1-based, append-only so they stay stable). Digest subject starts with `[Outbound] Action needed`.

## 7. Templates (parameterised)
Variables: first, company, line, achievement (rotated), second achievement, resume URL.
- Subject A: `Founder's Office at {company}`. Subject B: `{company} and a BD system that runs itself`. Chosen by hash of email, logged via the row.
- Email body: `Hi {first},` / `{line}` / `{achievement}` / `Most early teams need someone to own revenue and the system behind it before hiring a full desk. Open to a 15-minute call to see if that is useful for {company}?` / `Ayush`
- Achievements: (1) BD desk 0 to Rs 40 Cr GMV quarter, conversion 7% to 20%. (2) YC S21 vertical to Rs 9 Cr, Rs 2.7 Cr closed at 40% conversion. (3) Messy pipelines into revenue systems: Rs 9 Cr at AlgoUniversity, Rs 40 Cr GMV at Enout.
- Follow-up 1 (day 5): second achievement + "Happy to walk you through how."
- Follow-up 2 (day 12): "closing the loop", resume link.
- WhatsApp: `Hi {first}, Ayush here. I emailed about {company}. {achievement 1} Happy to share details here if easier.`
- Default `line`: `I came across {company} and wanted to reach out directly.` Better line when Gemini step exists (section 8).

## 8. Personal line (build later, optional)
- Gemini API, free tier. Inputs: company about, site pages /customers, /case-studies, /about (robots.txt respected, max 4 pages, 90 day cache), `proof.csv` (client, industry, persona, deal, name_usable).
- Match: same client > same industry + buyer persona > YC link (only if the site says it is YC backed). Only High confidence becomes a line; otherwise a one-sentence "what they build" line.
- If `name_usable` is false, describe by industry and persona, never the name.
- Max 30 words, first person, no flattery.

## 9. Config
Env: `GMAIL_USER`, `GMAIL_APP_PASSWORD` (Gmail IMAP enabled), optional `GEMINI_API_KEY`. Constants at top of file: DAILY_CAP, FU1_DAYS=5, FU2_DAYS=12, WA_DAYS=3, RESUME.

## 10. Build order (acceptance)
1. pytest with mocked smtplib/imaplib: draft dedupe and excludes, phone cleaning, reply grammar parsing, follow-up age logic, cap enforcement. Done = tests green.
2. Run `draft` on one real Apollo export; fix header mapping. Done = queue.csv looks right on 10 rows.
3. Live: one email to Ayush's second address, then a follow-up on a shortened FU1_DAYS; confirm Gmail threads them. Done = same thread.
4. Reply path: reply to the digest with each command; confirm queue.csv updates. Done = all four commands work.
5. Refill nudge (section 2.10).
6. Bounce handling: a delivery-failure email marks `replied=bounced`, stops the row, and counts toward the 3% stop.
7. Personal line (section 8).
8. Schedule: cron / persistent machine, log file for errors.

## 11. Not in scope
LinkedIn automation (invites stay manual, max 15/day), job-application autofill, Airtable, n8n, auto WhatsApp, open tracking.

## 12. Risks to keep visible
Client names while still employed at Enout (use Proof `name_usable`), primary Gmail reputation, Apollo free credit limits, SMTP app password stored only in env, never in repo.
