# Instructions for the agent (Tasks table only)

You work ONLY in the Airtable `Tasks` table. Do not touch any other table. Never send an email or message anyone.

## Loop
1. Take one row with `Status = Open`. Set `Status = Working` and `Started at = now`.
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
