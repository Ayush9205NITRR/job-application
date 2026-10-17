# Instructions for the agent (Tasks table only)

You work ONLY in the Airtable `Tasks` table. Do not touch any other table. Never send an email or message anyone.

## Where
Airtable base: https://airtable.com/appms9ERcB5Ws3r0w , table `Tasks`. Work in the Airtable web UI (you are signed in with Google).
Make a filter on the `Tasks` grid: `Status` is `Open`. Open a row by expanding it. Paste the Result as plain text in the `Result` cell.

## Loop
1. Take one row with `Status = Open`. Set `Status = Working` and fill `Started at` with the current date and time (UTC). If `Started at` is empty the task is treated as stuck and reopened.
2. Read `Type` and `Input` (JSON). Do exactly what that type asks (below).
3. Write the answer in `Result` as ONE JSON object, nothing else. Set `Confidence` to `High` only if you are sure, else `Low`. Set `Status = Done`.
4. Never invent. If you cannot tell, set `Confidence = Low`.

## verify_company
Input: `{"company": "...", "linkedin": "...", "search_results": [...]}`
Result: `{"same_company": true|false, "website": "https://...", "stage": "Pre-Seed|Seed|Series A|..."}`
`same_company` is true only if the search results clearly describe this exact company.

## pick_decision_makers
Input: `{"company": "...", "people": [{"name": "...", "title": "...", "linkedin": "..."}]}`
Result: `{"people": [{"name": "...", "title": "...", "role": "Founder|HR|TA", "linkedin": "https://linkedin.com/in/..."}]}`
Pick Founders / Co-founders, and HR or Talent Acquisition people. Several per company is fine. Skip everyone else.

## classify_post
Input: `{"company": "...", "linkedin": "...", "post": "first line of the post", "post_url": "..."}`. Open `post_url` if you can; otherwise judge from the company name and first line (then Confidence = Low if unsure).
Result: `{"kind": "startup|recruiter|other"}`
`recruiter` = staffing firm, job board, consultancy hiring for someone else. `other` = not a real hiring post. `startup` = the company is hiring for itself.

## Rules
- Result must be valid JSON: double quotes, no comments, no text before or after.
- Do not edit `Task ID`, `Type`, `Company`, `Company record`, `Input`. Do not touch `Note`, `Tries` (code owns them).
- Never set `Status` to `Applied`, `Failed` or `Needs human`. Code does that.
- Do not open any other table. Do not send email, LinkedIn requests or messages.
