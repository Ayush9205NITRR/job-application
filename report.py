"""One-screen daily summary for the GitHub run page (and the terminal). Read-only.
    python report.py"""
import os
from collections import Counter

import alerts
import scorecard
import tasks as T
from fo import airtable as at


def build(tasks=None, emails=None, contacts=None, your=None, companies=None):
    tasks = (tasks or T.Tasks).all()
    emails = (emails or at.Table("Emails")).all()
    mine = alerts.open_rows(your)
    t = Counter((r.get("Status")) for r in tasks)
    e = Counter(r.get("Status") for r in emails)
    no_email = sum(1 for c in (contacts if contacts is not None else at.Contacts.all()) if c.get("LinkedIn URL") and not c.get("Email"))
    card = scorecard.score(companies if companies is not None else at.Companies.all())
    lines = [f"## YOUR TASKS: {len(mine)} open"] + [f"- {r.get('Title')}" for r in mine]
    lines += ["", "## Agent work", f"- Open {t['Open']}, Working {t['Working']}, Done waiting for code {t['Done']}, Needs human {t['Needs human']}, Failed {t['Failed']}, Applied {t['Applied']}",
              "", "## Emails", f"- Ready for draft {e['Ready for Gmail draft']}, in Gmail drafts {e['In Gmail drafts']}, Sent {e['Sent']}",
              f"- People with LinkedIn but no email yet (your job): {no_email}", "", "## Sources"]
    lines += [f"- {s}: {v['companies']} companies, {int(v['rate'] * 100)}% kept" for s, v in sorted(card.items())]
    return "\n".join(lines)


if __name__ == "__main__":
    text = build()
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")
