#!/usr/bin/env python3
"""Write the cleaned data out as a tidy Excel file — one sheet per table, nothing to repair.

  python3 scripts/export_clean.py --file imports/Email_Outreach_Tracker.xlsx
  python3 scripts/export_clean.py --from-grist
  python3 scripts/export_clean.py --file <in.xlsx> --out somewhere/else.xlsx

Default output is out/<name>_clean.xlsx. `out/` is gitignored, which matters: these sheets hold real
names, email addresses and phone numbers.

The result can go straight back into imports/ — tracker_clean reads this layout as well as the
original — so it is a usable file, not just something to look at.
"""
import argparse
import datetime
import pathlib
import sys

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tracker_clean as TC  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Column order per table. Anything else the cleaner produced is appended after these.
ORDER = {
    "leads": ["lead_id", "recipient_name", "recipient_email", "company", "role", "designation",
              "linkedin", "campaign", "priority", "status", "owner", "followup_count",
              "next_action", "next_action_due", "notes", "created_at", "updated_at"],
    "email_log": ["recipient_name", "recipient_email", "company", "subject", "stage", "folder",
                  "sent_date", "reply_received", "thread_id", "message_id",
                  "followup_1_due", "followup_1_status", "followup_2_due", "followup_2_status", "notes"],
    "followup_drafts": ["recipient_name", "recipient_email", "company", "original_subject",
                        "followup_1_due", "followup_1_status", "followup_1_subject", "followup_1_body",
                        "followup_2_due", "followup_2_status", "followup_2_subject", "followup_2_body",
                        "notes"],
    "companies": ["company_name", "website", "domain", "city", "country", "stage", "amount",
                  "investors", "sector", "source", "status", "picked_at", "notes", "added_by"],
    "decision_makers": ["person_name", "title", "bucket", "company", "person_linkedin_url",
                        "company_linkedin_url", "source", "confidence", "found_at", "notes"],
    "deleted": ["recipient", "subject", "folder", "reason", "timestamp", "draft_id"],
}
# Internal bookkeeping that is not worth a column.
HIDE = {"_dup", "_shifted", "followup_2_status_dup", "scheduled_datetime", "original_msg_id", "sector"}

HEADER = Font(bold=True)
WRAP = Alignment(vertical="top", wrap_text=True)
LONG = {"followup_1_body", "followup_2_body", "notes", "detail", "reason", "subject",
        "original_subject", "followup_1_subject", "followup_2_subject"}


def pretty(name):
    return TC.PROVENANCE.get(name) or name.replace("_", " ").strip().capitalize()


def columns_for(key, rows):
    wanted = [c for c in ORDER.get(key, []) if any(c in r for r in rows)]
    extra = []
    for r in rows:
        for c in r:
            if c in HIDE or c in wanted or c in extra:
                continue
            if c.startswith("_") and c not in TC.PROVENANCE:
                continue
            extra.append(c)
    return wanted + extra


def write_sheet(wb, title, columns, rows, headers=None):
    ws = wb.create_sheet(title)
    ws.append(headers or [pretty(c) for c in columns])
    for cell in ws[1]:
        cell.font = HEADER
    for r in rows:
        ws.append([_cell(r.get(c)) for c in columns])
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
    for i, c in enumerate(columns, start=1):
        letter = get_column_letter(i)
        if c in LONG:
            ws.column_dimensions[letter].width = 52
            for cell in ws[letter][1:]:
                cell.alignment = WRAP
        else:
            longest = max([len(str(r.get(c) or "")) for r in rows] + [len(pretty(c))])
            ws.column_dimensions[letter].width = min(max(longest + 2, 10), 40)
    return ws


def _cell(v):
    if v is True:
        return "Yes"
    if v is False:
        return ""
    if v is None:
        return ""
    return v


def summary_rows(cleaned):
    s = TC.summarise(cleaned)
    r = s["rows"]
    return [
        ("Emails attempted", s["attempted"], "sent or bounced"),
        ("Delivered", s["delivered"], "attempted minus bounces"),
        ("Replied", s["replied"], ""),
        ("Bounced", s["bounced"], ""),
        ("Still queued", s["queued"], "drafts and scheduled, not yet sent"),
        ("", "", ""),
        ("Leads", r["leads"], f"{s['recovered_leads']} recovered from the crushed cell in Master row 72"),
        ("Email log rows", r["email_log"], "rebuilt from three blocks of the Followups sheet"),
        ("Follow-up drafts", r["followup_drafts"], "written, none sent"),
        ("Companies", r["companies"], ""),
        ("Decision makers", r["decision_makers"], ""),
        ("Deleted drafts", r["deleted"], ""),
        ("", "", ""),
        ("Rows with shifted columns", s["shifted_leads"], "re-read by layout, not dropped"),
        ("Leads with no address", s["leads_without_email"], "Master said 'No email'"),
        ("Problems found", s["issues"], f"{s['issues_high']} serious — see the Data issues sheet"),
    ]


def build(cleaned, out, source_note):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("Summary")
    ws.append(["Email outreach tracker, cleaned"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([source_note])
    ws.append([f"Made {datetime.datetime.now().strftime('%d %b %Y, %H:%M')}"])
    ws.append([])
    ws.append(["What", "Count", "Note"])
    for cell in ws[5]:
        cell.font = HEADER
    for row in summary_rows(cleaned):
        ws.append(list(row))
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 62

    for key, title in TC.TIDY_SHEETS.items():
        rows = cleaned["tables"][key]
        write_sheet(wb, title, columns_for(key, rows), rows)

    issues = cleaned.get("issues") or []
    write_sheet(wb, TC.ISSUES_SHEET, ["sheet", "kind", "detail", "sev"], issues,
                headers=["Sheet", "Type", "Detail", "Severity"])

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def from_grist():
    """Current Grist contents as the same kind of workbook.

    Dates cross the API as unix seconds, and guessing which columns those are from their names gets
    it wrong (Sent_1 is a date, Mails_Sent is not), so ask Grist for each column's type.
    """
    import grist_client as G

    tables = ["Companies", "Founders", "Outreach_Log", "Data_Issues", "Refresh_Runs"]
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name in tables:
        meta = G._call("GET", f"/docs/{G.DOC}/tables/{name}/columns")["columns"]
        dates = {c["id"] for c in meta if str(c["fields"].get("type", "")).startswith(("Date", "DateTime"))}
        rows = [r["fields"] for r in G.fetch(name)]
        cols = []
        for r in rows:
            for c in r:
                if c not in cols:
                    cols.append(c)
        for r in rows:
            for c in dates & set(r):
                v = r.get(c)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    r[c] = datetime.datetime.fromtimestamp(v, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
        write_sheet(wb, name, cols, rows, headers=cols)
    return wb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="the workbook to clean")
    ap.add_argument("--from-grist", action="store_true", help="export what is in Grist right now")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.from_grist == bool(a.file):
        sys.exit("choose one: --file <workbook.xlsx>  or  --from-grist")

    if a.from_grist:
        out = pathlib.Path(a.out or ROOT / "out" / "Grist_export.xlsx")
        out.parent.mkdir(parents=True, exist_ok=True)
        from_grist().save(out)
        print(f"wrote {out}")
        return

    src = pathlib.Path(a.file)
    if not src.exists():
        sys.exit(f"no such file: {src}")
    cleaned = TC.clean_workbook(src)
    out = pathlib.Path(a.out) if a.out else ROOT / "out" / f"{src.stem}_clean.xlsx"
    build(cleaned, out, f"Cleaned from {src.name}")
    s = TC.summarise(cleaned)
    print(f"wrote {out}")
    for name, n in s["rows"].items():
        print(f"  {TC.TIDY_SHEETS[name]:20} {n:>5}")
    print(f"  {s['issues']} problems listed on the {TC.ISSUES_SHEET} sheet")


if __name__ == "__main__":
    main()
