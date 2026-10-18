#!/usr/bin/env bash
# One day's code work. Used by the timer (schedule.yml) and by hand (taskplan.yml cmd daily-*).
#   bash daily.sh fetch    new companies from Apify (spends Apify credit; skipped when DRY_RUN=1)
#   bash daily.sh round    make tasks, apply answers, Emails rows, follow-ups, report
# DRY_RUN=1 reads Airtable and prints what it would write, writes nothing, spends nothing.
set -u
job="${1:-round}"
fail=0
step() { echo "::group::$*"; "$@" || { echo "STEP FAILED: $*"; fail=1; }; echo "::endgroup::"; }

if [ "${DRY_RUN:-}" != "1" ]; then
  step python tasks.py setup
  step python emails.py setup
  step python alerts.py setup
fi
pf=""; [ "$job" = "fetch" ] && [ "${DRY_RUN:-}" != "1" ] && pf="--apify"
python preflight.py $pf || { python alerts.py check; exit 1; }

if [ "$job" = "fetch" ]; then
  if [ "${DRY_RUN:-}" = "1" ]; then echo "DRY: would run Apify posts push (about 0.50 USD)"; else step python posts_push.py --per 50; fi
fi
step python taskplan.py round --max 10
step python followups.py
step python scorecard.py
step python report.py
python alerts.py check || fail=1
exit $fail
