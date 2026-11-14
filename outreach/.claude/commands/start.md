---
description: Guided setup — does everything it can, asks me for ONE manual thing at a time
---

You are walking me (Ayush) through setup with minimum effort from me. Reply in short Hinglish. ONE step per reply. No explanations, no options, no theory. Do every step you can yourself; when a step needs me, give ONE action in at most 2 lines, then stop and end with: "Ho jaye to /start phir se."

Check in this order and handle the FIRST item that is not done:

1. `.env` exists? If not: `cp .env.example .env`. Then ask me to fill only what is missing (GRIST_API_KEY, GRIST_DOC_ID, RESUME_URL) — see steps 2-3 for where to get them.
2. Grist running? Check `curl -s -o /dev/null -w "%{http_code}" http://localhost:8484`. If not 200: run `docker compose up -d` (if GRIST_DEFAULT_EMAIL in docker-compose.yml is still you@example.com, ask me for my email, put it there first). If Docker isn't installed, tell me to install Docker Desktop and stop.
3. Grist doc + API key: if GRIST_DOC_ID or GRIST_API_KEY is empty, tell me exactly: "Browser mein http://localhost:8484 kholo > New document (empty) > naam Outreach. URL se doc id copy karo. Phir Profile settings > API Key > create. Dono .env mein daal do." Stop.
4. Tables created / up to date? Always run `python3 scripts/setup_grist.py` (it is safe to re-run; it adds any missing tables/columns). Then tell me the one click: "Founders > Company column > Column options > SHOW COLUMN = Company."
5. Tests: `python3 tests/test_all.py` must pass; fix if not.
6. Gmail read access (for "kitno ko email gaya"): if `credentials.json` is missing, give me these steps in 5 short lines: Google Cloud Console > new project > enable Gmail API > OAuth consent External + add my email as test user > Credentials > OAuth client ID > Desktop app > download JSON > rename credentials.json > repo root. Then `pip install -r requirements.txt`. Stop.
7. Gmail drafts connector: check that a Gmail MCP server is connected (`/mcp`). If not, tell me to connect it. Confirm the send/reply/forward tool names are in the deny list of `.claude/settings.json` (fix names if they differ). Stop if I must act.
8. Templates: if the `Templates` table rows are empty, ask me to paste my 2 emails + 2 WhatsApps (+ LinkedIn note) in chat; you write them into Templates with placeholders {{first_name}} {{company}} {{hook}} {{proof}} {{role}}. Show me the result in 5 lines.
9. CSV: look in `imports/` for a .csv. If none: "CSV ko imports/ folder mein daal do." If found: run `/import-csv` on it.
10. Everything done: tell me to run `python3 ui/server.py` and open http://localhost:8585 (upload + dashboard live there), then the single next command (`/run-a` or `/run-b`). Nothing else.

Never send emails. Never write formula columns. Never skip a step to "save time".
