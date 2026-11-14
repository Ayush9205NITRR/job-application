#!/usr/bin/env python3
"""Read Email_Outreach_Tracker.xlsx and hand back tidy tables + a list of what is wrong with it.

Pure: no Grist, no network. import_tracker.py turns the output into Grist rows; tests call it directly.

  python3 scripts/tracker_clean.py imports/Email_Outreach_Tracker.xlsx        # counts + problems
  python3 scripts/tracker_clean.py imports/<file>.xlsx --json out.json        # the whole thing

The workbook needs three repairs before any of it can be read as a table:

1. Master's header is ONE tab-joined cell (A1), not 21 columns.
2. Master row 72, column A, holds ~10k characters: 17 whole leads mashed together with line breaks.
   Left alone those leads are invisible to every importer.
3. The Followups sheet is three pasted blocks at different column offsets, not one table:
     rows 2-100    cols A-P   follow-up drafts        (its own header, row 1, with 'fo rsa' for the email)
     rows 101-197  cols A-P   an email log, NO header of its own
     rows 198-239  cols N-AC  more of that same email log, shifted right
   The header at row 140 (cols Q-AC) describes only the log, and only its last 13 columns.

Nothing is dropped and nothing is invented: a row that cannot be parsed still comes back, and the
reason lands in `issues`.
"""
import datetime
import json
import pathlib
import re
import sys

import openpyxl

# Followups sheet geometry. Rows are 1-based as Excel shows them.
FU_DRAFTS_ROWS = (2, 100)
FU_LOG_A_ROWS = (101, 197)      # cols A-P
FU_LOG_B_ROWS = (198, None)     # cols N-AC, None = to the end
FU_LOG_B_FIRST_COL = 14         # column N

DRAFT_COLS = [
    "recipient_email", "recipient_name", "company", "original_subject", "original_msg_id", "thread_id",
    "followup_1_due", "followup_1_subject", "followup_1_body", "followup_1_status",
    "followup_2_due", "followup_2_subject", "followup_2_body", "followup_2_status",
    "followup_2_status_dup", "notes",
]
LOG_COLS = [
    "recipient_email", "recipient_name", "company", "subject", "stage", "folder", "sent_date",
    "scheduled_datetime", "thread_id", "message_id", "reply_received", "followup_1_due",
    "followup_1_status", "followup_2_due", "followup_2_status", "notes",
]
BLOB_COLS = [
    "lead_id", "recipient_email", "recipient_name", "company", "role", "campaign", "priority",
    "status", "owner", "original_subject", "last_activity_at", "followup_count", "next_action",
    "next_action_due", "notes", "created_at", "updated_at",
]

SENT_STAGES = ("SENT_NO_REPLY", "SENT_REPLIED")
ATTEMPTED_STAGES = SENT_STAGES + ("BOUNCED",)

EXCEL_EPOCH = datetime.datetime(1899, 12, 30)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_BLANKS = {"", "none", "nan", "null", "n/a", "-"}


def clean_str(v):
    """Collapse whitespace; treat the usual placeholder words as empty."""
    if v is None:
        return ""
    s = re.sub(r"\s+", " ", str(v).replace(" ", " ")).strip()
    return "" if s.lower() in _BLANKS else s


def parse_dt(v):
    """-> (YYYY-MM-DD HH:MM or None, problem or None). Dates arrive three different ways in this file."""
    if v in (None, ""):
        return None, None
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M"), None
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d 00:00"), None
    if isinstance(v, (int, float)):
        try:
            return (EXCEL_EPOCH + datetime.timedelta(days=float(v))).strftime("%Y-%m-%d %H:%M"), "excel_serial"
        except (ValueError, OverflowError):
            return None, "unparsed_date"
    s = str(v).strip()
    bare = re.sub(r"\s*(IST|UTC|GMT)\s*$", "", s)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(bare, fmt).strftime("%Y-%m-%d %H:%M"), None
        except ValueError:
            pass
    m = re.match(r"^[A-Za-z]{3}, (\d{1,2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2})", s)  # RFC-2822
    if m:
        try:
            return datetime.datetime.strptime(m.group(1), "%d %b %Y %H:%M:%S").strftime("%Y-%m-%d %H:%M"), None
        except ValueError:
            pass
    return None, "unparsed_date"


def norm_email(v):
    """-> (lowercased address, problem or None). Empty is not a problem; a non-address is."""
    s = clean_str(v).lower()
    if not s:
        return "", None
    return s, (None if EMAIL_RE.match(s) else "invalid_email")


class _Report:
    def __init__(self):
        self.issues = []

    def add(self, sheet, kind, detail, sev="warn"):
        self.issues.append({"sheet": sheet, "kind": kind, "detail": detail, "sev": sev})


def _row_values(ws, row, first_col, last_col):
    return [ws.cell(row, c).value for c in range(first_col, last_col + 1)]


def _is_blank(vals):
    return all(v in (None, "") for v in vals)


def _dates(rec, fields, rep, sheet, label=""):
    for f in fields:
        iso, problem = parse_dt(rec.get(f))
        rec[f] = iso or ""
        if problem == "excel_serial":
            rep.add(sheet, "excel_serial_date", f"{f} is a raw Excel number{label}", "high")
        elif problem:
            rep.add(sheet, "unparsed_date", f"{f} could not be read{label}")


def _email(rec, field, rep, sheet):
    rec[field], problem = norm_email(rec.get(field))
    if problem:
        rep.add(sheet, "invalid_email", rec[field] or "(blank)", "high")


# --------------------------------------------------------------------------- Master
def _split_blob(cell, rep):
    """Row 72 column A holds whole leads mashed together. Split them back out.

    Lines are separated by newlines and fields by runs of 2+ spaces (they were tabs once). A line can
    carry more than one lead, so cut again wherever a new lead_NNN token starts.
    """
    text = str(cell)
    chunks = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = re.split(r" {2,}", line)
        starts = [i for i, p in enumerate(parts) if re.fullmatch(r"lead_\d+", p)]
        for j, start in enumerate(starts):
            end = starts[j + 1] if j + 1 < len(starts) else len(parts)
            chunks.append(parts[start:end])
    rep.add("Master", "crushed_cell",
            f"Row 72 column A held {len(text)} characters = {len(chunks)} whole leads in ONE cell", "high")
    return chunks


_NO_EMAIL = {"no email", "noemail", "not found", "none", "na", "n a"}


def _relayout_lead(rec):
    """Give every lead row the same keys, whichever of the three layouts it was written in.

    Most rows follow the header. Some carry the literal "No email" instead of an address — those are
    fine, they just have no email. The rest were pasted in a different column order and would be junk
    if read against the header, but the values are all still there:

      A (priority holds a LinkedIn URL):
        email=company  name=sector  company=role  role=person  campaign=designation  priority=linkedin
      B:
        email=company  name=role    company=person  role=designation

    Returns the layout name; the record is rewritten in place.
    """
    email = (rec.get("recipient_email") or "").strip()
    if "@" in email:
        return "normal"
    if email.lower() in _NO_EMAIL:
        rec["recipient_email"] = ""
        return "no_email"

    company, name, co_cell, role_cell = email, rec.get("recipient_name", ""), \
        rec.get("company", ""), rec.get("role", "")
    if str(rec.get("priority", "")).startswith("http"):
        layout, person = "shifted_a", role_cell
        rec["designation"] = rec.get("campaign", "")
        rec["linkedin"] = rec.get("priority", "")
        rec["sector"] = name
        rec["role"] = co_cell
    else:
        layout, person = "shifted_b", co_cell
        rec["designation"] = role_cell
        rec["role"] = name
    rec["recipient_email"] = ""
    rec["recipient_name"] = person
    rec["company"] = company
    rec["campaign"] = ""
    rec["priority"] = ""
    return layout


def _read_master(wb, rep):
    ws = wb["Master"]
    header = [h.strip() for h in str(ws.cell(1, 1).value or "").split("\t")]
    rep.add("Master", "structure", f"Header row is ONE tab-joined cell, not {len(header)} columns", "high")

    rows, blob_row = [], None
    for r in range(2, ws.max_row + 1):
        vals = _row_values(ws, r, 1, len(header))
        if _is_blank(vals):
            continue
        if vals[0] is not None and len(str(vals[0])) > 1000:   # the crushed cell
            blob_row = vals[0]
            continue
        rec = {k: clean_str(v) for k, v in zip(header, vals)}
        rec["_recovered"] = False
        rows.append(rec)

    if blob_row is not None:
        for parts in _split_blob(blob_row, rep):
            rec = {k: clean_str(v) for k, v in zip(BLOB_COLS, parts)}
            rec["_recovered"] = True
            rows.append(rec)

    seen, layouts = {}, {}
    for rec in rows:
        rec.setdefault("designation", "")
        rec.setdefault("linkedin", "")
        rec.setdefault("sector", "")
        rec["_layout"] = _relayout_lead(rec)
        layouts[rec["_layout"]] = layouts.get(rec["_layout"], 0) + 1
        _email(rec, "recipient_email", rep, "Master")
        _dates(rec, ["first_sent_at", "last_activity_at", "next_action_due", "created_at", "updated_at"],
               rep, "Master", f" (lead {rec.get('lead_id') or '?'})")
        try:
            rec["followup_count"] = int(float(rec.get("followup_count") or 0))
        except (TypeError, ValueError):
            rec["followup_count"] = 0
        key = rec.get("recipient_email")
        if key and "@" in key:
            if key in seen:
                rep.add("Master", "duplicate_email", f"{key} (leads {seen[key]} and {rec.get('lead_id')})", "high")
                rec["_dup"] = True
            else:
                seen[key] = rec.get("lead_id")
    shifted = layouts.get("shifted_a", 0) + layouts.get("shifted_b", 0)
    if shifted:
        rep.add("Master", "shifted_columns",
                f"{shifted} rows were pasted in a different column order; re-read by layout", "high")
    if layouts.get("no_email"):
        rep.add("Master", "missing_value",
                f"{layouts['no_email']} leads carry the literal 'No email' instead of an address", "high")
    return rows


# --------------------------------------------------------------------------- Followups (3 blocks)
def _read_followups(wb, rep):
    ws = wb["Followups"]
    rep.add("Followups", "structure",
            "Sheet holds 3 pasted blocks at different column offsets, not one table", "high")
    rep.add("Followups", "bad_header", "Column A header reads 'fo rsa'; it is the recipient email", "high")
    rep.add("Followups", "duplicate_header", "'followup_2_status' appears twice (columns N and O)", "high")
    rep.add("Followups", "structure",
            f"Rows {FU_LOG_A_ROWS[0]}-{FU_LOG_A_ROWS[1]} are an email log with no header of their own", "high")
    rep.add("Followups", "structure",
            f"Rows {FU_LOG_B_ROWS[0]}+ are more of that log, shifted right to column N", "high")

    drafts = []
    for r in range(FU_DRAFTS_ROWS[0], min(FU_DRAFTS_ROWS[1], ws.max_row) + 1):
        vals = _row_values(ws, r, 1, 16)
        if _is_blank(vals):
            continue
        rec = {k: clean_str(v) for k, v in zip(DRAFT_COLS, vals)}
        _email(rec, "recipient_email", rep, "Followups")
        _dates(rec, ["followup_1_due", "followup_2_due"], rep, "Followups")
        if rec["followup_2_status"] and rec["followup_2_status_dup"] \
                and rec["followup_2_status"] != rec["followup_2_status_dup"]:
            rep.add("Followups", "conflicting_duplicate_column", rec["recipient_email"], "high")
        drafts.append(rec)

    log = []
    blocks = [(FU_LOG_A_ROWS[0], min(FU_LOG_A_ROWS[1], ws.max_row), 1),
              (FU_LOG_B_ROWS[0], ws.max_row, FU_LOG_B_FIRST_COL)]
    for first, last, col0 in blocks:
        for r in range(first, last + 1):
            vals = _row_values(ws, r, col0, col0 + 15)
            if _is_blank(vals):
                continue
            rec = {k: clean_str(v) for k, v in zip(LOG_COLS, vals)}
            _email(rec, "recipient_email", rep, "Followups")
            _dates(rec, ["sent_date", "scheduled_datetime", "followup_1_due", "followup_2_due"],
                   rep, "Followups")
            log.append(rec)

    seen = {}
    for rec in log:
        key = rec["recipient_email"]
        if key and "@" in key:
            if key in seen:
                rep.add("Followups", "duplicate_email", f"{key} appears twice in the log", "high")
                rec["_dup"] = True
            else:
                seen[key] = True
    return drafts, log


# --------------------------------------------------------------------------- plain sheets
def _read_plain(wb, name, width, rep, date_fields=(), email_field=None, dup_key=None):
    ws = wb[name]
    header = [clean_str(c) for c in _row_values(ws, 1, 1, width)]
    rows, seen = [], set()
    for r in range(2, ws.max_row + 1):
        vals = _row_values(ws, r, 1, width)
        if _is_blank(vals):
            continue
        rec = {k: clean_str(v) for k, v in zip(header, vals)}
        if email_field:
            _email(rec, email_field, rep, name)
        _dates(rec, list(date_fields), rep, name)
        if dup_key:
            key = tuple(rec.get(k, "").lower() for k in dup_key)
            if any(key) and key in seen:
                rep.add(name, "duplicate_row", " / ".join(x for x in key if x), "high")
                rec["_dup"] = True
            seen.add(key)
        rows.append(rec)
    return rows


def clean_workbook(path):
    """-> {"tables": {leads, email_log, followup_drafts, companies, decision_makers, deleted}, "issues": [...]}"""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    rep = _Report()

    leads = _read_master(wb, rep)
    followup_drafts, email_log = _read_followups(wb, rep)
    companies = _read_plain(wb, "Companies", 15, rep, ("picked_at",), dup_key=("company_name",))
    decision_makers = _read_plain(wb, "Decision_Makers", 10, rep, ("found_at",),
                                  dup_key=("person_name", "company"))
    deleted = _read_plain(wb, "Deleted_Log", 6, rep, ("timestamp",), email_field="recipient")

    for field in ("stage", "sector", "website", "city"):
        missing = sum(1 for c in companies if not c.get(field))
        if missing:
            rep.add("Companies", "missing_value", f"{field} blank in {missing}/{len(companies)} rows",
                    "high" if missing > len(companies) / 2 else "warn")

    known = {c["company_name"].lower() for c in companies if c.get("company_name")}
    named = set()
    for rows, field in ((leads, "company"), (email_log, "company"), (decision_makers, "company")):
        named |= {r[field].lower() for r in rows if r.get(field)}
    unknown = named - known
    if unknown:
        rep.add("cross-sheet", "company_not_listed",
                f"{len(unknown)} company names appear in other sheets but not in the Companies sheet", "high")

    def emails(rows, field="recipient_email"):
        return {r[field] for r in rows if r.get(field) and "@" in r[field]}

    lead_mails, log_mails = emails(leads), emails(email_log)
    rep.add("cross-sheet", "no_shared_key",
            f"Lead tracker has {len(lead_mails)} addresses and the email log {len(log_mails)}, "
            f"but only {len(lead_mails & log_mails)} are in both", "high")

    return {
        "tables": {
            "leads": leads,
            "email_log": email_log,
            "followup_drafts": followup_drafts,
            "companies": companies,
            "decision_makers": decision_makers,
            "deleted": deleted,
        },
        "issues": rep.issues,
    }


def summarise(cleaned):
    """Counts worth printing or asserting in a test."""
    log = cleaned["tables"]["email_log"]
    stage = lambda s: sum(1 for r in log if r.get("stage") == s)  # noqa: E731
    return {
        "rows": {k: len(v) for k, v in cleaned["tables"].items()},
        "attempted": sum(1 for r in log if r.get("stage") in ATTEMPTED_STAGES),
        "delivered": sum(1 for r in log if r.get("stage") in SENT_STAGES),
        "replied": stage("SENT_REPLIED"),
        "bounced": stage("BOUNCED"),
        "queued": stage("DRAFT") + stage("SCHEDULED"),
        "recovered_leads": sum(1 for r in cleaned["tables"]["leads"] if r.get("_recovered")),
        "shifted_leads": sum(1 for r in cleaned["tables"]["leads"]
                             if str(r.get("_layout", "")).startswith("shifted")),
        "leads_without_email": sum(1 for r in cleaned["tables"]["leads"] if r.get("_layout") == "no_email"),
        "issues": len(cleaned["issues"]),
        "issues_high": sum(1 for i in cleaned["issues"] if i["sev"] == "high"),
    }


def main(argv):
    if not argv:
        sys.exit(__doc__)
    path = pathlib.Path(argv[0])
    if not path.exists():
        sys.exit(f"no such file: {path}")
    cleaned = clean_workbook(path)
    if "--json" in argv:
        out = pathlib.Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(cleaned, ensure_ascii=False, default=str))
        print(f"wrote {out}")
    s = summarise(cleaned)
    for name, n in s["rows"].items():
        print(f"  {name:18} {n:>5}")
    print(f"\n  {s['attempted']} emails attempted: {s['delivered']} delivered "
          f"({s['replied']} replied), {s['bounced']} bounced. {s['queued']} still queued.")
    print(f"  {s['recovered_leads']} leads recovered from the crushed cell, "
          f"{s['shifted_leads']} rows have shifted columns.")
    print(f"  {s['issues']} problems ({s['issues_high']} serious).")


if __name__ == "__main__":
    main(sys.argv[1:])
