#!/usr/bin/env python3
"""Creates / upgrades the outreach schema inside an existing Grist document. Safe to re-run:
missing tables are created, missing columns are added to tables that already exist.

  1. docker compose up -d ; open http://localhost:8484 ; create an empty doc "Outreach"
  2. Profile settings > API key. Put GRIST_URL / GRIST_API_KEY / GRIST_DOC_ID in .env
  3. python3 scripts/setup_grist.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import formulas as F  # noqa: E402
import grist_client as G  # noqa: E402


def col(cid, ctype="Text", formula=None, choices=None, link=False):
    d = {"id": cid, "type": ctype}
    if formula:
        d["isFormula"] = True
        d["formula"] = formula
    wo = {}
    if choices:
        wo["choices"] = choices
    if link:
        wo["widget"] = "HyperLink"
    if wo:
        d["widgetOptions"] = json.dumps(wo)
    return d


FOUNDER_STATUS = ["New", "Contact-ready", "Ready", "Drafted-1", "Sent-1",
                  "Drafted-2", "Sent-2", "Replied", "Bounced", "Skip"]
STAGES = ["Not sent", "Sent", "Delivered", "Viewed", "Replied", "Bounced"]
# Where a row came from: the /run-a sourcing pipeline, or the imported outreach tracker.
SOURCE_SYSTEMS = ["pipeline", "tracker"]

COMPANY_STAGES = ["Seed", "Series A", "Series B", "Pre-Seed", "Pre-Series A", "Growth",
                  "Early Stage", "Bootstrapped", "Funded", "Public", "Unknown"]
COMPANY_STATUS = ["New", "Founders found", "Skip",
                  "Applied", "Applied & Drafted", "Outreach Drafted", "Partial", "Not_Found", "Done"]
DESIGNATIONS = ["Founder", "Co-Founder", "CEO", "COO",
                "Recruiter", "Talent Acquisition", "Hiring Manager", "Founding Member",
                "Leadership", "Other"]

COMPANIES = [
    col("Company"), col("Website"),
    col("Vertical", "Choice", choices=["Travel", "Edtech"]),
    col("Stage", "Choice", choices=COMPANY_STAGES),
    col("Last_Round_Date", "Date"), col("Round_Amount"),
    col("Funding_Source", link=True), col("Job_Link", link=True), col("Role_Target"),
    col("Funded_24mo", "Bool", F.F_FUNDED_24),
    col("Status", "Choice", choices=COMPANY_STATUS),
    col("Notes"),
    # from the tracker import. Vertical stays the /run-a Travel/Edtech choice; Sector is free text.
    col("Sector"), col("City"), col("Investors"), col("Picked_At", "Date"),
    col("Stage_Raw"),   # the stage exactly as the source wrote it, before it was tidied for charting
    col("Source_System", "Choice", choices=SOURCE_SYSTEMS),
]

FOUNDERS = [
    col("Name"), col("LinkedIn", link=True),
    col("Designation", "Choice", choices=DESIGNATIONS),
    col("Company", "Ref:Companies"), col("Company_Name", "Text", F.F_COMPANY_NAME),
    col("Email"), col("WhatsApp"),
    col("Email_norm", "Text", F.F_EMAIL_NORM),
    col("WA_norm", "Text", F.F_WA_NORM),
    col("LI_norm", "Text", F.F_LI_NORM),
    col("Is_Dup", "Bool", F.F_IS_DUP),
    col("Research_Hook"), col("Sources"),
    col("Email_Subject"), col("Email_Body"), col("Email2_Body"),
    col("WA_Msg"), col("WA2_Msg"), col("LI_Note"),
    col("WA_Link", "Text", F.F_WA_LINK, link=True),
    col("WA2_Link", "Text", F.F_WA2_LINK, link=True),
    col("Check_Before_Sending"),
    col("Approve", "Bool"),
    col("Status", "Choice", choices=FOUNDER_STATUS),
    col("Planned_Send", "DateTime:Asia/Kolkata"),
    # lifecycle (filled by reverse_map.py): Sent -> Delivered -> Viewed -> Replied
    col("Stage", "Choice", choices=STAGES),
    col("Thread_ID"),
    col("Sent_At", "DateTime:Asia/Kolkata"),
    col("Viewed_At", "DateTime:Asia/Kolkata"),
    col("Replied_At", "DateTime:Asia/Kolkata"),
    col("Mails_Sent", "Int"), col("Last_Sent", "Date"),
    col("Sent_1", "Date"), col("Sent_2", "Date"),
    # follow-up due dates (formulas)
    col("Sent2_Due", "Date", F.F_SENT2_DUE),
    col("WA1_Due", "Date", F.F_WA1_DUE),
    col("WA2_Due", "Date", F.F_WA2_DUE),
    # manual channels
    col("WA_Sent", "Bool"), col("WA2_Sent", "Bool"), col("LI_Sent", "Bool"),
    col("Reply", "Choice", choices=["None", "Replied", "Bounced", "Unsubscribed"]),
    col("Source"), col("Notes"),
    # the role this person was approached about. It is per-person, not per-company: the same company
    # comes up with different openings.
    col("Applied_Role"), col("Applied_Job_Link", link=True),
    col("Source_System", "Choice", choices=SOURCE_SYSTEMS),
    col("Parse_Flag"),
]

TEMPLATES = [col("Key"), col("Subject"), col("Body"), col("Notes")]

# What was wrong with an imported file, so the mess stays visible instead of being silently cleaned away.
DATA_ISSUES = [
    col("Sheet"), col("Kind"), col("Detail"),
    col("Severity", "Choice", choices=["high", "warn"]),
    col("Found_At", "DateTime:Asia/Kolkata"),
]

# One row per refresh run. Api_Calls matters: Grist's free plan allows 3000 API calls a month.
REFRESH_RUNS = [
    col("At", "DateTime:Asia/Kolkata"), col("Source"),
    col("Rows_Seen", "Int"), col("Added", "Int"), col("Updated", "Int"),
    col("Gmail_Checked", "Bool"), col("Api_Calls", "Int"), col("Errors", "Int"), col("Note"),
]

LOG = [
    col("Founder", "Ref:Founders"),
    col("Channel", "Choice", choices=["Email", "WhatsApp", "LinkedIn"]),
    col("Template"),
    col("Action", "Choice", choices=["Drafted", "Sent", "Skipped", "Reply", "Bounce", "Error"]),
    col("At", "DateTime:Asia/Kolkata"), col("Gmail_Msg_Id"), col("Note"),
]

SCHEMA = [("Companies", COMPANIES), ("Founders", FOUNDERS), ("Templates", TEMPLATES),
          ("Outreach_Log", LOG), ("Data_Issues", DATA_ISSUES), ("Refresh_Runs", REFRESH_RUNS)]


def widen_choices(name, have_cols, wanted_cols):
    """Add any missing Choice values to columns that already exist.

    AddColumn only covers new columns, so a column whose choice list grew (Designation gaining
    recruiter roles, Stage gaining Growth) needs ModifyColumn. Existing values are kept, including
    any the user added by hand in Grist.
    """
    wanted = {c["id"]: c for c in wanted_cols if c.get("widgetOptions")}
    actions = []
    for cur in have_cols:
        want = wanted.get(cur["id"])
        if not want:
            continue
        want_choices = json.loads(want["widgetOptions"]).get("choices")
        if not want_choices:
            continue
        opts = {}
        if cur["fields"].get("widgetOptions"):
            try:
                opts = json.loads(cur["fields"]["widgetOptions"])
            except ValueError:
                opts = {}
        now = opts.get("choices") or []
        missing = [c for c in want_choices if c not in now]
        if missing:
            opts["choices"] = now + missing
            actions.append(["ModifyColumn", name, cur["id"], {"widgetOptions": json.dumps(opts)}])
            print(f"     {name}.{cur['id']}: added choices {missing}")
    return actions


def main():
    if not (G.KEY and G.DOC):
        sys.exit("Set GRIST_API_KEY and GRIST_DOC_ID in .env")
    tables = G._call("GET", f"/docs/{G.DOC}/tables")["tables"]
    existing = {t["id"] for t in tables}
    actions = []

    if "Table1" in existing and not G.fetch("Table1"):
        actions.append(["RemoveTable", "Table1"])

    for name, cols in SCHEMA:
        if name in existing:
            print(f"ok   {name} (exists)")
        else:
            actions.append(["AddTable", name, cols])
            print(f"new  {name}")
    if actions:
        G._call("POST", f"/docs/{G.DOC}/apply", actions)

    # upgrade existing tables: add missing columns, then widen any Choice list that grew
    comp_cols = None
    for name, cols in SCHEMA:
        if name not in existing:
            continue
        have_cols = G._call("GET", f"/docs/{G.DOC}/tables/{name}/columns")["columns"]
        if name == "Companies":
            comp_cols = {c["id"] for c in have_cols}
        have = {c["id"] for c in have_cols}
        actions = []
        for c in cols:
            if c["id"] not in have:
                actions.append(["AddColumn", name, c["id"], {k: v for k, v in c.items() if k != "id"}])
        if actions:
            print(f"     {name}: added columns {[a[2] for a in actions]}")
        actions += widen_choices(name, have_cols, cols)
        if actions:
            G._call("POST", f"/docs/{G.DOC}/apply", actions)

    if comp_cols is not None and "Founders_Count" not in comp_cols:
        G._call("POST", f"/docs/{G.DOC}/apply", [[
            "AddColumn", "Companies", "Founders_Count",
            {"type": "Int", "isFormula": True, "formula": F.F_FOUNDERS_COUNT}]])
        print("     Companies: added Founders_Count")

    have = {r["fields"].get("Key") for r in G.fetch("Templates")}
    seed = [k for k in ["email_1", "email_2", "wa_1", "wa_2", "li_note"] if k not in have]
    if seed:
        G.add("Templates", [{"Key": k} for k in seed])
        print("     seeded Templates:", ", ".join(seed))
    print("\nDone. (First time only) Founders > Company column > Column options > SHOW COLUMN = Company.")


if __name__ == "__main__":
    main()
