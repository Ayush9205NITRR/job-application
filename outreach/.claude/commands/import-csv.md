---
description: Map and import a CSV into Grist, then report who has already been emailed
argument-hint: <path-to-csv>   (e.g. imports/list.csv)
---

Import the CSV at $ARGUMENTS into Grist and report the basics.

1. Run `python3 scripts/import_csv.py $ARGUMENTS --dry-run`. Read the detected mapping. If any column is wrong or missing (e.g. "Name" picked up the company, or a phone column wasn't found), write a mapping override to `config/csv_mapping.json` (format: `{"Company": "<csv header>", ...}`) and re-run the dry run with `--map config/csv_mapping.json`. Do not guess: if a column is ambiguous, ask me.
2. Show me: row count, companies, people, skipped rows, in-file duplicates, designation split, how many have email / WhatsApp / LinkedIn. Wait for my "go" only if something looks off; otherwise continue.
3. Run the real import (same command without `--dry-run`, plus `--map` if used). Report how many Founders rows got `Is_Dup = true`.
4. Run `python3 scripts/gmail_sync.py --dry-run`. Show the counts: checked, already_emailed, replied, bounced. Then run `--apply` and confirm.
5. Final report (short table): total people, unique people, with email, with WhatsApp, already emailed, replied, bounced, ready to personalise (Status = Contact-ready, Is_Dup = false, not yet emailed).

Never send anything. Never write formula columns.
