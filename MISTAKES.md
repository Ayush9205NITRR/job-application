# Mistakes made once, and the guard that stops the next one

| Mistake | Guard now |
|---|---|
| `AIRTABLE_BASE_ID` secret pointed at the old base, so data landed in the wrong place | Workflows pin the base id; `preflight.py` fails on a different base; agent Step 0 checks the URL |
| Agent left `reason` empty and rubber-stamped answers | Code sends the task back (2 tries, then Failed) |
| Agent typed IST into a UTC field | `requeue_stale` tolerates it; AGENT.md says UTC |
| Missing Airtable column crashed `apply` | Falls back to saving the Website; `preflight.py` checks columns |
| `setup` lost its field lists in an edit (NameError) | Test `test_setup_field_lists_exist` |
| `round` called with a wrong argument name | Test runs the real call |
| Same company worked twice from two sources | Dedupe on company LinkedIn URL in code and in AGENT.md |
| Agent might spend Apollo credits | AGENT.md rule 0, Apollo for LinkedIn URLs only, code ignores agent emails |
| Apify or Airtable limit stopped a run silently | `alerts.guard` writes a row in `Your tasks`; failed run emails you |
| New source floods the list with noise | `scorecard.py`: under 20% kept with 20+ rows raises a row; start each source small |
| Flow changed while a run was live | RUNBOOK rule: freeze during a run |

Add a row here every time something goes wrong, and add the check in the same change.
