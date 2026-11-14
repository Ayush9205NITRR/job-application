#!/usr/bin/env python3
"""Creates the outreach schema inside an existing (empty) Grist document. Safe to re-run.

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

COMPANIES = [
    col("Company"), col("Website"),
    col("Vertical", "Choice", choices=["Travel", "Edtech"]),
    col("Stage", "Choice", choices=["Seed", "Series A", "Series B"]),
    col("Last_Round_Date", "Date"), col("Round_Amount"),
    col("Funding_Source", link=True), col("Job_Link", link=True), col("Role_Target"),
    col("Funded_24mo", "Bool", F.F_FUNDED_24),
    col("Status", "Choice", choices=["New", "Founders found", "Skip"]),
    col("Notes"),
]

FOUNDERS = [
    col("Name"), col("LinkedIn", link=True),
    col("Designation", "Choice", choices=["Founder", "Co-Founder", "CEO", "COO"]),
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
    # email history (filled by gmail_sync.py)
    col("Mails_Sent", "Int"), col("Last_Sent", "Date"),
    col("Sent_1", "Date"), col("Sent_2", "Date"),
    col("Sent2_Due", "Date", F.F_SENT2_DUE),
    # manual channels
    col("WA_Sent", "Bool"), col("WA2_Sent", "Bool"), col("LI_Sent", "Bool"),
    col("Reply", "Choice", choices=["None", "Replied", "Bounced", "Unsubscribed"]),
    col("Source"), col("Notes"),
]

TEMPLATES = [col("Key"), col("Subject"), col("Body"), col("Notes")]

LOG = [
    col("Founder", "Ref:Founders"),
    col("Channel", "Choice", choices=["Email", "WhatsApp", "LinkedIn"]),
    col("Template"),
    col("Action", "Choice", choices=["Drafted", "Sent", "Skipped", "Reply", "Bounce", "Error"]),
    col("At", "DateTime:Asia/Kolkata"), col("Gmail_Msg_Id"), col("Note"),
]


def main():
    if not (G.KEY and G.DOC):
        sys.exit("Set GRIST_API_KEY and GRIST_DOC_ID in .env")
    tables = G._call("GET", f"/docs/{G.DOC}/tables")["tables"]
    existing = {t["id"] for t in tables}
    actions = []

    if "Table1" in existing and not G.fetch("Table1"):
        actions.append(["RemoveTable", "Table1"])

    for name, cols in [("Companies", COMPANIES), ("Founders", FOUNDERS),
                       ("Templates", TEMPLATES), ("Outreach_Log", LOG)]:
        if name in existing:
            print(f"skip {name} (exists)")
        else:
            actions.append(["AddTable", name, cols])
            print(f"create {name}")
    if actions:
        G._call("POST", f"/docs/{G.DOC}/apply", actions)

    comp_cols = {c["id"] for c in G._call("GET", f"/docs/{G.DOC}/tables/Companies/columns")["columns"]}
    if "Founders_Count" not in comp_cols:
        G._call("POST", f"/docs/{G.DOC}/apply", [[
            "AddColumn", "Companies", "Founders_Count",
            {"type": "Int", "isFormula": True, "formula": F.F_FOUNDERS_COUNT}]])
        print("added Companies.Founders_Count")

    have = {r["fields"].get("Key") for r in G.fetch("Templates")}
    seed = [k for k in ["email_1", "email_2", "wa_1", "wa_2", "li_note"] if k not in have]
    if seed:
        G.add("Templates", [{"Key": k} for k in seed])
        print("seeded Templates:", ", ".join(seed))
    print("\nDone. In Founders > Company column > Column options > SHOW COLUMN = Company.")


if __name__ == "__main__":
    main()
