# Instructions for the agent (Tasks table only)

You work ONLY in the Airtable `Tasks` table. Never touch another table. Never send an email, LinkedIn request or message.

## Where
Base: https://airtable.com/appms9ERcB5Ws3r0w , table `Tasks`. Use the Airtable web UI (signed in with Google).
Filter the `Tasks` grid: `Status` is `Open`. Expand a row to work on it.

## Loop (one task at a time)
1. Pick one `Open` row. Set `Status = Working`. Set `Started at` = current time in **UTC** (not IST; IST minus 5:30).
2. Read `Type` and `Input` (JSON). Do what that type asks (below). Open links when you can.
3. Write ONE JSON object in `Result`. Nothing before or after it.
4. Set `Confidence`: `High` only if you saw clear evidence. If you guessed, or could not open the link, set `Low`.
5. Set `Status = Done`.

## classify_post
Input: `{"company", "linkedin", "post", "post_url"}`
Result: `{"kind": "...", "reason": "one short line"}`

| kind | Use when |
|---|---|
| `startup` | A product company or startup hiring for ITSELF (any stage, small team) |
| `recruiter` | Staffing firm, job board, HR agency, or a post about jobs at OTHER companies |
| `services_firm` | IT services, consulting, outsourcing or agency firm hiring for itself. Not our target |
| `other` | Not a hiring post: news, ad, advice, event, profile, walk-in drive spam |

Examples:
- "We're hiring a Founder's Office associate" from a 10-person AI product company -> `startup`
- "53 open climate jobs this week" from a jobs newsletter -> `recruiter`
- "Aelum is calling. Your next career move" from a ServiceNow consulting partner -> `services_firm`
- Coworking ad titled "The Office Decision Founders Make" -> `other`

`reason` must name the evidence (what you saw), not repeat the label.

## verify_company
Input: `{"company", "linkedin", "search_results": [...]}`
Result: `{"same_company": true|false, "website": "https://...", "stage": "Pre-Seed|Seed|Series A|...", "reason": "..."}`
`same_company` is true only if the results clearly describe this exact company. If unsure, `Confidence = Low`.

## pick_decision_makers
Input: `{"company", "people": [{"name", "title", "linkedin"}]}`
Result: `{"people": [{"name", "title", "role": "Founder|HR|TA", "linkedin": "https://linkedin.com/in/..."}]}`
Pick Founders and Co-founders, plus HR and Talent Acquisition. Several per company is fine. Skip the rest.

## Rules
- Result is valid JSON: double quotes, no comments.
- Do not edit `Task ID`, `Type`, `Company`, `Company record`, `Input`, `Note`, `Tries`.
- Never set `Status` to `Applied`, `Failed` or `Needs human`. Code does that.
- Never invent. When unsure: `Confidence = Low`.
