#!/usr/bin/env python3
"""Keep Grist current: pick up new files in imports/, then ask Gmail what happened to the mail.

  python3 scripts/refresh.py --dry-run        # say what would change
  python3 scripts/refresh.py --apply          # do it
  python3 scripts/refresh.py --apply --skip-gmail

Meant to run on a schedule (see .github/workflows/outreach-refresh.yml). Every run writes a row to
Refresh_Runs, Api_Calls included: Grist's free plan allows 3000 API calls a month across the whole
site, so three runs a day leaves plenty of room.

Exits non-zero when it cannot do its job, so a failing schedule is visible instead of quietly passing.
"""
import argparse
import hashlib
import json
import pathlib
import sys
import traceback
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grist_client as G  # noqa: E402
import import_csv as IC  # noqa: E402
import import_tracker as IT  # noqa: E402
import reverse_map as RM  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / ".refresh_state.json"
IMPORTS = ROOT / "imports"


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def check_grist():
    """Fail early and clearly rather than halfway through a run."""
    if not (G.KEY and G.DOC):
        sys.exit("No Grist credentials: set GRIST_API_KEY and GRIST_DOC_ID (.env or the environment).")
    try:
        G._call("GET", f"/docs/{G.DOC}/tables")
    except SystemExit as e:
        sys.exit(f"Cannot reach Grist at {G.URL}: {e}")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach Grist at {G.URL}: {e.reason}")


def pending_files(state):
    """Files in imports/ that are new or have changed since the last run."""
    out = []
    if not IMPORTS.is_dir():
        return out
    for path in sorted(IMPORTS.iterdir()):
        if path.name.startswith(".") or path.suffix.lower() not in (".xlsx", ".csv"):
            continue
        if path.name.lower().startswith("views"):     # reverse_map reads these itself
            continue
        d = digest(path)
        if state.get(path.name) != d:
            out.append((path, d))
    return out


def import_file(path, dry):
    """-> (added, updated, note)"""
    if path.suffix.lower() == ".xlsx":
        r = IT.run(path, dry=dry, quiet=True)
        return (r["companies_add"] + r["people_add"] + r["log_add"] + r["issues_add"],
                r["companies_update"] + r["people_update"],
                f"{path.name}: {r['people_total']} people, {r['companies_total']} companies")
    rows = IC.read_rows(str(path))
    if not rows:
        return 0, 0, f"{path.name}: empty"
    mapping = IC.detect_mapping(list(rows[0].keys()), None)
    if dry:
        plan = IC.analyse(rows, mapping)
        return 0, 0, f"{path.name}: would add {plan['people']} people, {plan['companies']} companies"
    r = IC.write_import(rows, mapping, source=path.name)
    return r["founders_added"] + r["new_companies"], 0, \
        f"{path.name}: +{r['founders_added']} people, +{r['new_companies']} companies"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-gmail", action="store_true")
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        sys.exit("choose --dry-run or --apply")
    dry = not a.apply

    check_grist()
    state = load_state()
    added = updated = errors = 0
    notes, seen = [], 0

    for path, d in pending_files(state):
        seen += 1
        try:
            n_add, n_upd, note = import_file(path, dry)
            added += n_add
            updated += n_upd
            notes.append(note)
            if not dry:
                state[path.name] = d
        except Exception as e:                       # one bad file must not stop the rest
            errors += 1
            notes.append(f"{path.name}: FAILED {type(e).__name__}: {e}")
            traceback.print_exc()

    gmail_note = "skipped"
    if not a.skip_gmail:
        try:
            r = RM.run(apply=not dry, skip_gmail=False, quiet=True)
            updated += r["updated"]
            gmail_note = f"{r['founders']} checked, {r['updated']} updated"
        except SystemExit as e:                      # missing credentials, usually
            errors += 1
            gmail_note = f"FAILED: {e}"
        except Exception as e:
            errors += 1
            gmail_note = f"FAILED {type(e).__name__}: {e}"
            traceback.print_exc()

    note = "; ".join(notes) if notes else "nothing new in imports/"
    print(f"files changed: {seen} | added {added} | updated {updated} | gmail: {gmail_note}")
    print(f"  {note}")

    if not dry:
        STATE.write_text(json.dumps(state, indent=1))
        try:
            G.add("Refresh_Runs", [{
                "At": G.ts_now(), "Source": "refresh.py",
                "Rows_Seen": seen, "Added": added, "Updated": updated,
                "Gmail_Checked": not a.skip_gmail and not gmail_note.startswith("FAILED"),
                "Api_Calls": G.calls() + 1, "Errors": errors,
                "Note": (gmail_note + " | " + note)[:400],
            }])
        except SystemExit as e:
            print(f"could not write Refresh_Runs: {e}", file=sys.stderr)
            errors += 1

    print(f"  Grist API calls: {G.calls()}")
    if errors:
        sys.exit(f"{errors} step(s) failed")


if __name__ == "__main__":
    main()
