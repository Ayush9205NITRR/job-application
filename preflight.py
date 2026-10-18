"""Run before anything else. Fails loudly (exit 1) and writes a row to 'Your tasks' when the setup is wrong.
    python preflight.py            check base id, secrets, tables, columns
Past mistakes live in MISTAKES.md; each one that can be checked by code is checked here."""
import os
import sys

import alerts
from fo import airtable as at
from fo.airtable import _headers, base_id
from fo.http import request

EXPECTED_BASE = "appms9ERcB5Ws3r0w"
NEED_ENV = ("AIRTABLE_TOKEN", "AIRTABLE_BASE_ID")
NEED_COLUMNS = {"Companies": ("Company LinkedIn URL", "Status", "Source", "Funding stage", "Hook"), "Runs": ("Run", "Keyword", "New"), "Tasks": ("Task ID", "Status", "Result"),
                "Emails": ("To", "Status", "Step"), "Contacts": ("Email", "Email status", "LinkedIn URL"), "Your tasks": ("Key", "Status", "Mailed")}


def problems(env=None, tables=None, expected=EXPECTED_BASE, need=NEED_ENV):
    """Pure: -> list of (key, message). `tables` is {name: [column names]} or None when it could not be read."""
    env = os.environ if env is None else env
    out = [("missing_secret", f"missing {n}: add it in GitHub > Settings > Secrets > Actions") for n in need if not env.get(n)]
    base = env.get("AIRTABLE_BASE_ID", "")
    if base and expected not in base:
        out.append(("wrong_base", f"AIRTABLE_BASE_ID points at another base than {expected}"))
    if tables is not None:
        for t, cols in NEED_COLUMNS.items():
            if t not in tables:
                out.append(("missing_table", f"table {t} is missing: run the Task plan workflow, cmd setup"))
            else:
                out += [("missing_column", f"{t}.{c} is missing: run setup") for c in cols if c not in tables[t]]
    return out


def read_tables():
    meta = f"https://api.airtable.com/v0/meta/bases/{base_id()}/tables"
    return {t["name"]: [f["name"] for f in t["fields"]] for t in request("GET", meta, headers=_headers()).json()["tables"]}


def main():
    need = NEED_ENV + ("APIFY_TOKEN",) if "--apify" in sys.argv else NEED_ENV
    bad = problems(need=need)
    tables = None
    if not [k for k, _ in bad if k in ("wrong_base", "missing_secret")]:
        try:
            tables = read_tables()
            bad = problems(tables=tables, need=need)
        except RuntimeError as e:
            bad.append(("airtable_unreachable", str(e)[:200]))
    for key, msg in bad:
        print("PREFLIGHT FAIL:", msg)
    if bad and not any(k == "missing_table" and "Your tasks" in m for k, m in bad):
        for key, msg in bad[:3]:
            alerts.raise_alert("preflight_" + key, "Setup check failed: " + msg, "Fix it, then set this row to Done. See RUNBOOK.md.")
    print("preflight ok" if not bad else f"{len(bad)} problems")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
